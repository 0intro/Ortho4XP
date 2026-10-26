#!/usr/bin/env python3

# Check the imagery providers live: load them as Ortho4XP does, fetch the
# imagery of a ZL 16 tile inside the coverage of each, and print a line per
# provider: its code, then ok and the mean color of the image, blank and
# the color of an image of one color, fail and the answers of the server,
# or skip with api_key for a provider that needs a key, and with point for
# a provider in a directory without a point below. With arguments, check
# only the providers they name. Exits 1 if one is blank or fails.

import math
import os
import sys
sys.dont_write_bytecode = True

root_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path[:0] = [os.path.join(root_dir, "src"), os.path.join(root_dir, "Providers")]

import numpy
import O4_Imagery_Utils as IMG
import O4_UI_Utils as UI

usage = "usage: providers.py [code ...]"

# A point, as (lat, lon), inside the coverage of the providers of each
# directory, and of the providers that cover a part of their country.
directory_points = {
    "Austria": (48.2082, 16.3738),
    "Belgium": (50.4674, 4.8720),
    "Croatia": (45.8150, 15.9819),
    "Czech_Republic_and_Slovakia": (50.0755, 14.4378),
    "Denmark": (55.6761, 12.5683),
    "Estonia": (59.4370, 24.7536),
    "Faroe": (62.0079, -6.7900),
    "Finland": (60.1699, 24.9384),
    "France": (48.8566, 2.3522),
    "Global": (43.2965, 5.3698),
    "Italy": (41.9028, 12.4964),
    "Japan": (35.6762, 139.6503),
    "Luxembourg": (49.6116, 6.1319),
    "Netherland": (52.3676, 4.9041),
    "New_Zealand": (-41.2865, 174.7762),
    "Norway": (59.9139, 10.7522),
    "Poland": (52.2297, 21.0122),
    "Slovenia": (46.0569, 14.5058),
    "Spain": (41.6488, -0.8891),
    "Sweden": (59.3293, 18.0686),
    "Switzerland": (46.9480, 7.4474),
    "United_States": (38.9072, -77.0369),
}
provider_points = {
    "Alaska": (61.2181, -149.9003),
    "CA_NAIP": (38.5816, -121.4944),
    "CH_VS": (46.2331, 7.3606),
    "CH_ZH": (47.3769, 8.5417),
    "GeoPunt2012": (51.0543, 3.7174),
    "GeoPunt2015": (51.0543, 3.7174),
    "Itris": (47.2692, 11.4041),
    "Lomba2015": (45.4642, 9.1900),
}

# The half width of a ZL 16 tile in web mercator meters.
half = 20037508.34 / 2 ** 16

# The answers of the servers to the requests that failed.
answers = []
request_to_image = IMG.http_request_to_image

def recording_request(width, height, url, request_headers, http_session):
    (ok, data) = request_to_image(width, height, url, request_headers, http_session)
    if not ok:
        answers.append(str(data))
    return (ok, data)

def mercator(lat, lon):
    r = 6378137
    return (r * math.radians(lon), r * math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)))

def check(provider):
    point = provider_points.get(provider["code"], directory_points.get(provider["directory"]))
    if point is None:
        return ("skip", "point")
    (x, y) = mercator(*point)
    answers.clear()
    (ok, image) = IMG.build_texture_from_bbox_and_size((x - half, y + half, x + half, y - half), "3857", (256, 256), provider)
    if not ok:
        return ("fail", " ".join(sorted(set(answers))) or "no image")
    image = image.convert("RGB")
    mean = numpy.asarray(image, dtype = float).mean(axis = (0, 1))
    color = ",".join(str(round(c)) for c in mean)
    if all(lo == hi for (lo, hi) in image.getextrema()):
        return ("blank", color)
    return ("ok", color)

if __name__ == "__main__":
    UI.log = False
    IMG.http_request_to_image = recording_request
    IMG.initialize_extents_dict()
    IMG.initialize_color_filters_dict()
    IMG.initialize_providers_dict()
    codes = sorted(set(IMG.providers_dict) | IMG.providers_without_key)
    if any(code not in codes for code in sys.argv[1:]):
        print(usage, file = sys.stderr)
        sys.exit(2)
    failed = False
    for code in sys.argv[1:] or codes:
        if code in IMG.providers_without_key:
            (status, detail) = ("skip", "api_key")
        else:
            (status, detail) = check(IMG.providers_dict[code])
        failed = failed or status in ("blank", "fail")
        print(code, status, detail, flush = True)
    sys.exit(1 if failed else 0)
