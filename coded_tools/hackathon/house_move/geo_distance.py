"""Straight-line distances for the local services finders."""

import math


class GeoDistance:
    """
    Distances in kilometres, from latitude/longitude (haversine) or British National Grid metres (Pythagoras).
    Straight-line distance only: it says nothing about catchment areas or practice boundaries.
    """

    EARTH_RADIUS_KM: float = 6371.0

    @staticmethod
    def between_lat_lon(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        """
        :return: the great-circle distance in km.
        """
        phi1, phi2 = math.radians(lat1), math.radians(lat2)
        d_phi: float = math.radians(lat2 - lat1)
        d_lambda: float = math.radians(lon2 - lon1)
        a: float = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
        return 2 * GeoDistance.EARTH_RADIUS_KM * math.asin(math.sqrt(a))

    @staticmethod
    def between_grid(easting1: float, northing1: float, easting2: float, northing2: float) -> float:
        """
        :return: the distance in km between two British National Grid points given in metres.
        """
        return math.hypot(easting2 - easting1, northing2 - northing1) / 1000.0
