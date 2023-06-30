import json
import os
from typing import List, Tuple
import requests
import pandas as pd
from pathlib import Path
from dotenv import load_dotenv
from copy import deepcopy

MIN_COORDS = (48.11, 16.21)
MAX_COORDS = (48.30, 16.56)
STEP_COUNT = 20


def get_coords() -> List[Tuple[float, float]]:
    lat_step = (MAX_COORDS[0] - MIN_COORDS[0]) / (STEP_COUNT - 1)
    long_step = (MAX_COORDS[1] - MIN_COORDS[1]) / (STEP_COUNT - 1)

    coords = []
    for lat_idx in range(STEP_COUNT):
        lat = MIN_COORDS[0] + (lat_idx * lat_step)

        for long_idx in range(STEP_COUNT):
            long = MIN_COORDS[1] + (long_idx * long_step)

            coords.append((round(lat, 4), round(long, 4)))

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


def get_rating(query: str) -> float:
    api_key = os.environ["MAPS_API_KEY"]
    url = "https://maps.googleapis.com/maps/api/place/findplacefromtext/json"
    params = dict(input=query, inputtype="textquery", fields="rating", key=api_key)
    resp = requests.get(url=url, params=params, verify=True)  # TODO: activate verify
    place = resp.json()

    if (not place["status"] == "OK") or (not place["candidates"][0]):
        return

    return place["candidates"][0]["rating"]


def filter_restaurants_add_ratings(places, count_process=None):
    out_path = Path("output", f"restaurants_with_ratings.json")

    if out_path.exists():
        with open(out_path) as f:
            restaurants = json.load(f)
    else:
        restaurants = [place for place in places if place["RS"] in [1, "1"]]

    count_done = 0
    for idx, restaurant in enumerate(restaurants):
        if not "rating" in restaurant:
            if count_process == count_done:
                break

            query = f"{restaurant['Vertragspartner']}, {restaurant['Adresse']}, {restaurant['Stadt']}"
            try:
                rating = get_rating(query)
            except requests.exceptions.ConnectionError:
                # TODO: warn
                break
            restaurants[idx]["rating"] = rating

            count_done += 1

    with open(out_path, "w") as f:
        json.dump(restaurants, f)

    return restaurants


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
    write_geojson(restaurants)
