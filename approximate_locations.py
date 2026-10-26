"""Interpret FMCSA written distance/bearing/city location descriptions.
No geocoding: estimates are relative to the same named reference place.
"""
import math
import re
from validate_single_driver_locations import normalize_location

COMPASS = {name: i * 22.5 for i, name in enumerate(
    ('N','NNE','NE','ENE','E','ESE','SE','SSE','S','SSW','SW','WSW','W','WNW','NW','NNW'))}
OFFSET = re.compile(r'^\s*(\d+(?:\.\d+)?)\s*(mi|miles?|km|kilometers?)\s+(NNE|ENE|ESE|SSE|SSW|WSW|WNW|NNW|NE|SE|SW|NW|N|E|S|W)\s+(.+?)\s*$', re.I)

def parse_offset(text, exclude_zip=True):
    m=OFFSET.match(str(text or ''))
    if not m: return None
    amount=float(m.group(1)) * (0.621371 if m.group(2).lower().startswith('k') else 1)
    bearing=COMPASS[m.group(3).upper()]
    place=normalize_location(m.group(4),exclude_zip)
    return amount, bearing, place

def compare_approximate(a, b, exclude_zip=True, threshold_miles=0.5):
    """Return (result, estimated relative displacement in miles, explanation).
    A pass here means consistent within the configured *approximation* tolerance,
    not a proven GPS coordinate match. Different cities remain a mismatch.
    """
    x,y=parse_offset(a,exclude_zip),parse_offset(b,exclude_zip)
    if not x or not y: return None
    if x[2]!=y[2]: return ('LOCATION_MISMATCH',None,'different_reference_places')
    angle=math.radians(x[1]-y[1])
    distance=math.sqrt(max(0,x[0]**2+y[0]**2-2*x[0]*y[0]*math.cos(angle)))
    if distance<=threshold_miles:
        return ('PASS',round(distance,3),'approximate_offset_within_tolerance')
    return ('REVIEW_APPROXIMATE_LOCATION',round(distance,3),'approximate_offset_needs_review')
