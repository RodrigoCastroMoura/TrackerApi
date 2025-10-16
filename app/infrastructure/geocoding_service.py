"""
Geocoding service using Nominatim (OpenStreetMap) for reverse geocoding.

This service converts GPS coordinates (latitude, longitude) into human-readable addresses.
It implements rate limiting to comply with Nominatim's usage policy (1 request/second).
"""

from geopy.geocoders import Nominatim
from geopy.exc import GeocoderTimedOut, GeocoderUnavailable
import logging
import time
from functools import lru_cache
from typing import Optional, Dict

logger = logging.getLogger(__name__)


class GeocodingService:
    """
    Service for reverse geocoding using Nominatim.
    
    Features:
    - Rate limiting (1 request/second as per Nominatim policy)
    - Caching for frequently requested coordinates
    - Error handling with graceful fallbacks
    """
    
    def __init__(self):
        # Initialize Nominatim with a proper user agent
        self.geolocator = Nominatim(
            user_agent="docsmart_vehicle_tracking/1.0",
            timeout=10
        )
        self.last_request_time = 0
        self.min_delay = 1.0  # 1 second between requests (Nominatim policy)
    
    def _rate_limit(self):
        """Ensure we don't exceed Nominatim's rate limit (1 req/sec)."""
        elapsed = time.time() - self.last_request_time
        if elapsed < self.min_delay:
            sleep_time = self.min_delay - elapsed
            time.sleep(sleep_time)
        self.last_request_time = time.time()
    
    @lru_cache(maxsize=1000)
    def reverse_geocode(self, lat: float, lng: float, language: str = 'pt') -> Optional[str]:
        """
        Convert coordinates to address.
        
        Args:
            lat: Latitude
            lng: Longitude
            language: Language for the address (default: 'pt' for Portuguese)
        
        Returns:
            Address string or None if geocoding fails
        """
        try:
            # Apply rate limiting
            self._rate_limit()
            
            # Round coordinates to 4 decimal places for caching
            # (4 decimals = ~11 meters precision, good enough for caching)
            lat_rounded = round(lat, 4)
            lng_rounded = round(lng, 4)
            
            # Perform reverse geocoding
            location = self.geolocator.reverse(
                f"{lat_rounded}, {lng_rounded}",
                language=language,
                addressdetails=True
            )
            
            if location:
                return location.address
            else:
                logger.warning(f"No address found for coordinates: {lat}, {lng}")
                return None
                
        except GeocoderTimedOut:
            logger.error(f"Geocoding timeout for coordinates: {lat}, {lng}")
            return None
        except GeocoderUnavailable:
            logger.error("Nominatim service unavailable")
            return None
        except Exception as e:
            logger.error(f"Geocoding error: {str(e)}")
            return None
    
    @lru_cache(maxsize=1000)
    def reverse_geocode_detailed(self, lat: float, lng: float, language: str = 'pt') -> Optional[Dict]:
        """
        Convert coordinates to detailed address components.
        
        Args:
            lat: Latitude
            lng: Longitude
            language: Language for the address
        
        Returns:
            Dictionary with address components or None
        """
        try:
            # Apply rate limiting
            self._rate_limit()
            
            # Round coordinates for caching
            lat_rounded = round(lat, 4)
            lng_rounded = round(lng, 4)
            
            # Perform reverse geocoding
            location = self.geolocator.reverse(
                f"{lat_rounded}, {lng_rounded}",
                language=language,
                addressdetails=True
            )
            
            if location and location.raw:
                address_data = location.raw.get('address', {})
                return {
                    'full_address': location.address,
                    'road': address_data.get('road', ''),
                    'house_number': address_data.get('house_number', ''),
                    'suburb': address_data.get('suburb', ''),
                    'city': address_data.get('city', address_data.get('town', address_data.get('village', ''))),
                    'state': address_data.get('state', ''),
                    'postcode': address_data.get('postcode', ''),
                    'country': address_data.get('country', ''),
                    'country_code': address_data.get('country_code', '').upper()
                }
            else:
                logger.warning(f"No detailed address found for coordinates: {lat}, {lng}")
                return None
                
        except GeocoderTimedOut:
            logger.error(f"Geocoding timeout for coordinates: {lat}, {lng}")
            return None
        except GeocoderUnavailable:
            logger.error("Nominatim service unavailable")
            return None
        except Exception as e:
            logger.error(f"Geocoding error: {str(e)}")
            return None
    
    def get_address_or_fallback(self, lat: float, lng: float) -> str:
        """
        Get address with automatic fallback to coordinates if geocoding fails.
        
        Args:
            lat: Latitude
            lng: Longitude
        
        Returns:
            Address string or formatted coordinates
        """
        address = self.reverse_geocode(lat, lng)
        if address:
            return address
        else:
            # Fallback to coordinates
            return f"{lat:.6f}, {lng:.6f}"


# Singleton instance
_geocoding_service = None

def get_geocoding_service() -> GeocodingService:
    """Get or create the singleton geocoding service instance."""
    global _geocoding_service
    if _geocoding_service is None:
        _geocoding_service = GeocodingService()
    return _geocoding_service
