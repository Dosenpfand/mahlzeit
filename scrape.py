import json
import logging
import os
from typing import List, Tuple, Union
import requests
import pandas as pd
from pathlib import Path
from dotenv import load_dotenv
from copy import deepcopy
from geopy.distance import distance

MIN_COORDS = (48.11, 16.21)
MAX_COORDS = (48.30, 16.56)
STEP_COUNT = 20


def get_coords() -> List[Tuple[float, float]]:
    lat_step = (MAX_COORDS[0] - MIN_COORDS[0]) / (STEP_COUNT - 1)
    lon_step = (MAX_COORDS[1] - MIN_COORDS[1]) / (STEP_COUNT - 1)

    coords = []
    for lat_idx in range(STEP_COUNT):
        lat = MIN_COORDS[0] + (lat_idx * lat_step)

        for lon_idx in range(STEP_COUNT):
            lon = MIN_COORDS[1] + (lon_idx * lon_step)

            coords.append((round(lat, 4), round(lon, 4)))

    return coords


def scrape_sodexo(coords: List[Tuple[float, float]]) -> None:
    url = "https://suche.einloesestellen.at/partners.json"
    user_agent = (
        "Mozilla/5.0 (X11; Linux x86_64; rv:104.0) Gecko/20100101 Firefox/104.0"
    )
    headers = {"User-Agent": user_agent}

    for coord in coords:
        out_path = Path("output", f"places_{coord[0]}_{coord[1]}.json")

        if out_path.exists():
            continue

        params = dict(lat=coord[0], lng=coord[1])
        resp = requests.get(
            url=url, params=params, verify=False, headers=headers
        )  # TODO: activate verify
        places_current = resp.json()

        with open(out_path, "w") as f:
            json.dump(places_current, f)


def merge_and_clean_dump(coords: List[Tuple[float, float]]):
    out_path = Path("output", f"places_merged_and_cleaned.json")

    if out_path.exists():
        with open(out_path) as f:
            return json.load(f)

    places_merged = []
    for coord in coords:
        in_path = Path("output", f"places_{coord[0]}_{coord[1]}.json")

        with open(in_path) as f:
            places = json.load(f)

        places_merged.extend(places)

    places_cleaned = pd.DataFrame.from_dict(places_merged).drop_duplicates()

    with open(out_path, "w") as f:
        json.dump(places_cleaned.to_dict(orient="records"), f)

    return places_cleaned


def google_request(query: str, fields: Union[List[str], str]):
    api_key = os.environ["MAPS_API_KEY"]
    url = "https://maps.googleapis.com/maps/api/place/findplacefromtext/json"

    fields_str = ",".join(fields) if isinstance(fields, list) else fields
    params = dict(input=query, inputtype="textquery", fields=fields_str, key=api_key)
    resp = requests.get(url=url, params=params)
    place = resp.json()

    if (not place["status"] == "OK") or (not place["candidates"][0]):
        return

    return place


def get_rating_google(query: str) -> float:
    place = google_request(query, "rating")

    if place:
        return place["candidates"][0]["rating"]


def get_coords_google(query):
    place = google_request(query, "geometry")

    if place:
        return place["candidates"][0]["geometry"]["location"]


def filter_restaurants_add_ratings(places, count_process=None):
    out_path = Path("output", f"restaurants_with_ratings.json")

    if out_path.exists():
        with open(out_path) as f:
            restaurants = json.load(f)
    else:
        restaurants = [
            place
            for place in places.to_dict(orient="records")
            if place["RS"] in [1, "1"]
        ]

    count_done = 0
    for idx, restaurant in enumerate(restaurants):
        if not "rating" in restaurant:
            if count_process == count_done:
                break

            query = f"{restaurant['Vertragspartner']}, {restaurant['Adresse']}, {restaurant['Stadt']}"
            try:
                rating = get_rating_google(query)
            except requests.exceptions.ConnectionError:
                logging.warning("Connection error, skipping remaining items.")
                break
            restaurants[idx]["rating"] = rating

            count_done += 1

    with open(out_path, "w") as f:
        json.dump(restaurants, f)

    return restaurants


def update_low_resolution(restaurants, resolution=2):
    out_path = Path("output", f"restaurants_increased_resolution.json")

    if out_path.exists():
        with open(out_path) as f:
            restaurants = json.load(f)

    for idx, restaurant in enumerate(restaurants):
        lng_dec_len = len(str(restaurant["lng"]).split(".")[1])
        lat_dec_len = len(str(restaurant["lat"]).split(".")[1])

        if (
            (lng_dec_len <= resolution)
            and (lat_dec_len <= resolution)
            and (not restaurant.get("low_res"))
        ):
            query = (
                f"{restaurant['Vertragspartner']},"
                # f" {restaurant['Adresse']}," TODO!
                f" {restaurant['Stadt']},"
                f" Austria"
            )
            coords = get_coords_nominatim(query)

            if coords:
                restaurants[idx]["lng"] = coords[0]
                restaurants[idx]["lat"] = coords[1]
            else:
                try:
                    coords = get_coords_google(query)
                except requests.exceptions.ConnectionError:
                    logging.warning("Connection error, skipping remaining items.")
                    break

                if coords:
                    restaurants[idx]["lng"] = coords["lng"]
                    restaurants[idx]["lat"] = coords["lat"]
                else:
                    restaurants[idx]["low_res"] = True

    with open(out_path, "w") as f:
        json.dump(restaurants, f)

    return restaurants


def delete_duplicates(restaurants, max_dist=50):
    out_path = Path("output", f"restaurants_deleted_duplicates.json")

    if out_path.exists():
        with open(out_path) as f:
            restaurants = json.load(f)

    idxs_to_delete = set()
    for idx, restaurant in enumerate(restaurants):
        for idx_other, restaurant_other in enumerate(restaurants[idx + 1 :]):
            if restaurant["Vertragspartner"] == restaurant_other["Vertragspartner"]:
                dist = distance(
                    (restaurant["lat"], restaurant["lng"]),
                    (restaurant_other["lat"], restaurant_other["lng"]),
                )
                if dist < max_dist:
                    idxs_to_delete.add(idx + 1 + idx_other)

    restaurants = [
        restaurant
        for idx, restaurant in enumerate(restaurants)
        if idx not in idxs_to_delete
    ]

    with open(out_path, "w") as f:
        json.dump(restaurants, f)

    return restaurants


def get_coords_nominatim(query):
    url = "   https://nominatim.openstreetmap.org/search"
    user_agent = "https://github.com/Dosenpfand/mahlzeit"
    headers = {"User-Agent": user_agent}

    params = dict(
        q=query,
        format="json",
    )
    resp = requests.get(url=url, params=params, headers=headers)
    nom_result = resp.json()

    if not nom_result:
        return None

    if isinstance(nom_result, list):
        nom_result = nom_result[0]
        # TODO: take closer one, not first one

    if "lat" in nom_result:
        return (nom_result["lon"], nom_result["lat"])
    if "centroid" in nom_result:
        return tuple(nom_result["centroid"]["coordinates"])


def write_geojson(restaurants):
    # TODO: check for output file existence

    feature_template = dict(
        geometry=dict(type="Point", coordinates=[None, None]),
        type="Feature",
        properties=dict(),
        id=None,
    )

    features = []
    for idx, restaurant in enumerate(restaurants):
        feature = deepcopy(feature_template)
        feature["geometry"]["coordinates"][0] = restaurant["lng"]
        feature["geometry"]["coordinates"][1] = restaurant["lat"]
        feature["properties"]["name"] = restaurant["Vertragspartner"]
        feature["properties"]["rating"] = restaurant.get("rating")
        feature["id"] = idx

        features.append(feature)

    output = dict(type="FeatureCollection", features=features)

    out_path = Path("output", f"restaurants.js")
    with open(out_path, "w") as f:
        f.write("var restaurants = ")
        json.dump(output, f)


if __name__ == "__main__":
    load_dotenv()
    coords = get_coords()
    scrape_sodexo(coords)
    places = merge_and_clean_dump(coords)
    restaurants = filter_restaurants_add_ratings(places)
    restaurants = update_low_resolution(restaurants)
    restaurants = delete_duplicates(restaurants)
    write_geojson(restaurants)
