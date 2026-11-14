# flight-dashboard
This repository contains code for a flight LED dashboard.

`flights.py` selects up to five airborne aircraft closest to the configured
`LAT`/`LON`, within the existing OpenSky bounding box. Aircraft below 10,000 feet
are excluded. The cutoff and displayed altitude use barometric altitude, falling
back to geometric altitude when barometric altitude is missing.

Ranking uses approximate 3D distance in meters: the hypotenuse of the haversine
surface distance and geometric altitude (barometric altitude if missing). The
observer reference height is sea level; this is a local-distance approximation,
not a terrain-adjusted distance. The terminal table keeps its existing columns.
States without a callsign, position, or altitude are skipped.

adsbdb responses are checked for HTTP errors before parsing. Unknown routes
(including HTTP 404) and routes with missing airport codes are retried after
five minutes; complete routes remain cached for the process lifetime. Other
HTTP errors, network failures, and malformed responses propagate to the polling
loop's error output and are not cached as unknown routes. Passenger-style flight
numbers are used when available, otherwise the OpenSky callsign is retained.
