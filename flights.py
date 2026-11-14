import json
import math
import time

import requests

# Downtown Des Moines, ~25 miles box (0.36 deg lat ~ 25 mi; 0.48 deg lon ~ 25 mi at this latitude)
LAT, LON = 41.59, -93.62
BOX_LAT, BOX_LON = 0.36, 0.48
MAX_ROWS = 5
POLL_SECONDS = 60
FEET_PER_METER = 3.28084
MIN_ALTITUDE_FT = 10000
EARTH_RADIUS_M = 6371000
UNKNOWN_ROUTE_RETRY_SECONDS = 300

TOKEN_URL = "https://auth.opensky-network.org/auth/realms/opensky-network/protocol/openid-connect/token"
STATES_URL = "https://opensky-network.org/api/states/all"
ROUTE_URL = "https://api.adsbdb.com/v0/callsign/"

with open("credentials.json") as f:
    CREDS = json.load(f)

token, token_expires = None, 0
routes = {}  # callsign -> ((flight number, origin, destination), expiry)


def get_token():
    global token, token_expires
    if token and time.time() < token_expires:
        return token
    r = requests.post(TOKEN_URL, data={
        "grant_type": "client_credentials",
        "client_id": CREDS["clientId"],
        "client_secret": CREDS["clientSecret"],
    }, timeout=10)
    r.raise_for_status()
    data = r.json()
    token = data["access_token"]
    token_expires = time.time() + data.get("expires_in", 1800) - 60
    return token


def get_states():
    params = {"lamin": LAT - BOX_LAT, "lamax": LAT + BOX_LAT, "lomin": LON - BOX_LON, "lomax": LON + BOX_LON}
    r = requests.get(STATES_URL, params=params, headers={"Authorization": f"Bearer {get_token()}"}, timeout=10)
    r.raise_for_status()
    return r.json()["states"] or []


def get_route(callsign):
    cached = routes.get(callsign)
    if cached and time.monotonic() < cached[1]:
        return cached[0]

    r = requests.get(ROUTE_URL + callsign, timeout=10)
    try:
        r.raise_for_status()
    except requests.HTTPError:
        if r.status_code != 404:
            raise
        route = None  # adsbdb returns 404 for unknown callsigns
    else:
        route = r.json()["response"]

    result = (callsign, "---", "---")
    if isinstance(route, dict) and "flightroute" in route:
        fr = route["flightroute"]
        # Passenger-style flight number (UAL123 -> UA123); keep raw callsign if adsbdb has no usable one
        iata = fr.get("callsign_iata")
        flight = iata if iata and not iata.isdigit() else callsign
        result = (flight, fr["origin"]["iata_code"] or "---", fr["destination"]["iata_code"] or "---")

    expiry = float("inf") if "---" not in result[1:] else time.monotonic() + UNKNOWN_ROUTE_RETRY_SECONDS
    routes[callsign] = (result, expiry)
    return result


def distance_m(latitude, longitude, altitude):
    lat, observer_lat = math.radians(latitude), math.radians(LAT)
    delta_lat = lat - observer_lat
    delta_lon = math.radians(longitude - LON)
    a = math.sin(delta_lat / 2) ** 2 + math.cos(observer_lat) * math.cos(lat) * math.sin(delta_lon / 2) ** 2
    horizontal = 2 * EARTH_RADIUS_M * math.asin(math.sqrt(min(1, a)))
    return math.hypot(horizontal, altitude)

 
def get_flights():
    flights = []
    for s in get_states():
        callsign, on_ground = (s[1] or "").strip(), s[8]
        longitude, latitude = s[5], s[6]
        altitude = s[7] if s[7] is not None else s[13]
        geo_altitude = s[13] if s[13] is not None else altitude
        if not callsign or on_ground or latitude is None or longitude is None or altitude is None:
            continue
        if altitude * FEET_PER_METER < MIN_ALTITUDE_FT:
            continue
        flights.append({
            "callsign": callsign,
            "altitude_ft": round(altitude * FEET_PER_METER),
            "distance_m": distance_m(latitude, longitude, geo_altitude),
        })

    flights = sorted(flights, key=lambda f: f["distance_m"])[:MAX_ROWS]
    for f in flights:
        f["callsign"], f["origin"], f["destination"] = get_route(f["callsign"])
    return flights


if __name__ == "__main__":
    while True:
        try:
            print(f"\n{'FLIGHT':<9}{'FROM':<6}{'TO':<6}{'ALT (ft)':>8}")
            for f in get_flights():
                print(f"{f['callsign']:<9}{f['origin']:<6}{f['destination']:<6}{f['altitude_ft']:>8}")
        except Exception as e:
            print("Error:", e)
        time.sleep(POLL_SECONDS)
