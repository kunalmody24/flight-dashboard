import importlib.util
from pathlib import Path
import unittest
from unittest.mock import Mock, mock_open, patch

import requests


spec = importlib.util.spec_from_file_location("flights", Path(__file__).resolve().parents[1] / "flights.py")
flights = importlib.util.module_from_spec(spec)
with patch("builtins.open", mock_open(read_data="{}")):
    spec.loader.exec_module(flights)


def state(callsign="UAL123", latitude=flights.LAT, longitude=flights.LON,
          baro=4000, geo=4100, on_ground=False):
    row = [None] * 14
    row[1], row[5], row[6] = callsign, longitude, latitude
    row[7], row[8], row[13] = baro, on_ground, geo
    return row


def route_response(iata="UA123", origin="ORD", destination="DEN"):
    return {"response": {"flightroute": {
        "callsign_iata": iata,
        "origin": {"iata_code": origin},
        "destination": {"iata_code": destination},
    }}}


class FlightTests(unittest.TestCase):
    def setUp(self):
        flights.routes.clear()
        # Fail closed if a test accidentally reaches an unmocked network call.
        self.network = patch.object(flights.requests, "get", side_effect=AssertionError("Unexpected network call"))
        self.network.start()
        self.addCleanup(self.network.stop)
        self.post = patch.object(flights.requests, "post", side_effect=AssertionError("Unexpected network call"))
        self.post.start()
        self.addCleanup(self.post.stop)

    def get_flights(self, states):
        with patch.object(flights, "get_states", return_value=states), patch.object(
            flights, "get_route", side_effect=lambda callsign: (callsign, "ORD", "DEN")
        ) as lookup:
            result = flights.get_flights()
        return result, lookup

    def test_distance_uses_horizontal_and_vertical_components(self):
        self.assertAlmostEqual(flights.distance_m(flights.LAT, flights.LON, 4000), 4000)
        self.assertAlmostEqual(flights.distance_m(flights.LAT + 0.1, flights.LON, 0), 11119.49, places=2)
        self.assertAlmostEqual(
            flights.distance_m(flights.LAT + 0.1, flights.LON, 4000),
            (11119.492664 ** 2 + 4000 ** 2) ** 0.5,
            places=3,
        )

    def test_nearest_first_limits_rows_before_route_lookups(self):
        states = [state(f"PLANE{i}", latitude=flights.LAT + i * 0.02, geo=4000) for i in range(7)]
        states.append(state("HIGH", baro=10000, geo=10000))
        result, lookup = self.get_flights(list(reversed(states)))
        self.assertEqual([f["callsign"] for f in result], [f"PLANE{i}" for i in range(5)])
        self.assertEqual(lookup.call_count, 5)

    def test_altitude_cutoff_and_invalid_states(self):
        cutoff = flights.MIN_ALTITUDE_FT / flights.FEET_PER_METER
        result, _ = self.get_flights([
            state("BOUNDARY", baro=cutoff),
            state("BELOW", baro=cutoff - 0.01),
            state("GROUND", on_ground=True),
            state("NO_LAT", latitude=None),
            state("NO_LON", longitude=None),
            state("NO_ALT", baro=None, geo=None),
            state("   "),
            state("ZERO_BARO", baro=0, geo=5000),
            state("GEO_LOW", baro=None, geo=3000),
        ])
        self.assertEqual([f["callsign"] for f in result], ["BOUNDARY"])
        self.assertEqual(result[0]["altitude_ft"], 10000)

    def test_geo_distance_and_baro_display_with_altitude_fallbacks(self):
        result, _ = self.get_flights([
            state("GEO", baro=4000, geo=6000),
            state("BARO_ONLY", baro=5000, geo=None),
            state("GEO_ONLY", baro=None, geo=4500),
        ])
        self.assertEqual([f["callsign"] for f in result], ["GEO_ONLY", "BARO_ONLY", "GEO"])
        self.assertEqual([f["distance_m"] for f in result], [4500, 5000, 6000])
        self.assertEqual(result[-1]["altitude_ft"], round(4000 * flights.FEET_PER_METER))

    def test_zero_coordinates_are_valid(self):
        result, _ = self.get_flights([state(latitude=0, longitude=0)])
        self.assertEqual(len(result), 1)

    def test_successful_routes_are_checked_and_cached(self):
        response = Mock()
        response.json.side_effect = lambda: (
            self.assertTrue(response.raise_for_status.called) or route_response()
        )
        with patch.object(flights.requests, "get", return_value=response) as get:
            self.assertEqual(flights.get_route("UAL123"), ("UA123", "ORD", "DEN"))
            with patch.object(flights.time, "monotonic", return_value=10**12):
                self.assertEqual(flights.get_route("UAL123"), ("UA123", "ORD", "DEN"))
        get.assert_called_once_with(flights.ROUTE_URL + "UAL123", timeout=10)

    def test_passenger_number_fallbacks(self):
        for iata in (None, "", "123", "UA123"):
            with self.subTest(iata=iata):
                flights.routes.clear()
                response = Mock()
                response.json.return_value = route_response(iata=iata)
                with patch.object(flights.requests, "get", return_value=response):
                    self.assertEqual(flights.get_route("UAL123")[0], "UA123" if iata == "UA123" else "UAL123")

    def test_unknown_and_partial_routes_expire(self):
        for payload in ({"response": "unknown callsign"}, route_response(destination=None)):
            with self.subTest(payload=payload):
                flights.routes.clear()
                response = Mock()
                response.json.side_effect = [payload, route_response()]
                with patch.object(flights.requests, "get", return_value=response) as get, patch.object(
                    flights.time, "monotonic", return_value=100
                ) as clock:
                    first = flights.get_route("UAL123")
                    self.assertIn("---", first)
                    clock.return_value = 399
                    self.assertEqual(flights.get_route("UAL123"), first)
                    self.assertEqual(get.call_count, 1)
                    clock.return_value = 400
                    self.assertEqual(flights.get_route("UAL123"), ("UA123", "ORD", "DEN"))
                    self.assertEqual(get.call_count, 2)

    def test_404_is_cached_temporarily_without_parsing_error_body(self):
        response = Mock(status_code=404)
        response.raise_for_status.side_effect = requests.HTTPError("Not found")
        with patch.object(flights.requests, "get", return_value=response) as get, patch.object(
            flights.time, "monotonic", return_value=100
        ) as clock:
            self.assertEqual(flights.get_route("UAL123"), ("UAL123", "---", "---"))
            flights.get_route("UAL123")
            self.assertEqual(get.call_count, 1)
            clock.return_value = 400
            flights.get_route("UAL123")
            self.assertEqual(get.call_count, 2)
        response.json.assert_not_called()

    def test_transient_and_malformed_responses_are_not_cached(self):
        for code in (429, 500):
            with self.subTest(code=code):
                response = Mock(status_code=code)
                response.raise_for_status.side_effect = requests.HTTPError(str(code))
                with patch.object(flights.requests, "get", return_value=response), self.assertRaises(requests.HTTPError):
                    flights.get_route("UAL123")
                response.json.assert_not_called()
                self.assertNotIn("UAL123", flights.routes)
        with patch.object(flights.requests, "get", side_effect=requests.Timeout), self.assertRaises(requests.Timeout):
            flights.get_route("UAL123")
        response = Mock()
        response.json.side_effect = ValueError("Invalid JSON")
        with patch.object(flights.requests, "get", return_value=response), self.assertRaises(ValueError):
            flights.get_route("UAL123")
        self.assertNotIn("UAL123", flights.routes)


if __name__ == "__main__":
    unittest.main()
