"""
Geocoding service using Nominatim (OpenStreetMap) and Google Maps for reverse geocoding.

This service converts GPS coordinates (latitude, longitude) into human-readable addresses.
It implements rate limiting and caching for optimal performance.

Providers:
- Nominatim (OpenStreetMap): Free, rate-limited (1 req/sec)
- Google Maps: Paid, requires GOOGLE_MAPS_API_KEY environment variable
"""

from geopy.geocoders import Nominatim
from geopy.exc import GeocoderTimedOut, GeocoderUnavailable
import logging
import time
import os
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


class GoogleGeocodingService:
    """
    Service for reverse geocoding using Google Maps Geocoding API.
    
    Features:
    - High-quality address data from Google Maps
    - Caching for frequently requested coordinates
    - Error handling with graceful fallbacks
    - Requires GOOGLE_MAPS_API_KEY environment variable
    """
    
    def __init__(self, api_key: Optional[str] = None):
        """
        Initialize Google Maps geocoding service.
        
        Args:
            api_key: Google Maps API key. If None, reads from GOOGLE_MAPS_API_KEY env var.
        
        Raises:
            ValueError: If API key is not provided and not found in environment
        """
        self.api_key = api_key or os.getenv('GOOGLE_MAPS_API_KEY')
        
        if not self.api_key:
            raise ValueError(
                "Google Maps API key not found. "
                "Set GOOGLE_MAPS_API_KEY environment variable or pass api_key parameter."
            )
        
        try:
            import googlemaps
            self.client = googlemaps.Client(key=self.api_key)
        except ImportError:
            raise ImportError(
                "googlemaps package not installed. "
                "Install it with: pip install googlemaps"
            )
    
    @lru_cache(maxsize=1000)
    def reverse_geocode(self, lat: float, lng: float, language: str = 'pt') -> Optional[str]:
        """
        Convert coordinates to address using Google Maps.
        
        Args:
            lat: Latitude
            lng: Longitude
            language: Language for the address (default: 'pt' for Portuguese)
        
        Returns:
            Address string or None if geocoding fails
        """
        try:
            # Round coordinates to 4 decimal places for caching
            lat_rounded = round(lat, 4)
            lng_rounded = round(lng, 4)
            
            # Perform reverse geocoding
            results = self.client.reverse_geocode(
                (lat_rounded, lng_rounded),
                language=language
            )
            
            if results and len(results) > 0:
                return results[0]['formatted_address']
            else:
                logger.warning(f"No address found for coordinates: {lat}, {lng}")
                return None
                
        except Exception as e:
            logger.error(f"Google Maps geocoding error: {str(e)}")
            return None
    
    @lru_cache(maxsize=1000)
    def reverse_geocode_detailed(self, lat: float, lng: float, language: str = 'pt') -> Optional[Dict]:
        """
        Convert coordinates to detailed address components using Google Maps.
        
        Args:
            lat: Latitude
            lng: Longitude
            language: Language for the address
        
        Returns:
            Dictionary with address components or None
        """
        try:
            # Round coordinates for caching
            lat_rounded = round(lat, 4)
            lng_rounded = round(lng, 4)
            
            # Perform reverse geocoding
            results = self.client.reverse_geocode(
                (lat_rounded, lng_rounded),
                language=language
            )
            
            if not results or len(results) == 0:
                logger.warning(f"No detailed address found for coordinates: {lat}, {lng}")
                return None
            
            # Extract address components
            result = results[0]
            components = {}
            
            for component in result.get('address_components', []):
                types = component.get('types', [])
                long_name = component.get('long_name', '')
                short_name = component.get('short_name', '')
                
                if 'street_number' in types:
                    components['house_number'] = long_name
                elif 'route' in types:
                    components['road'] = long_name
                elif 'sublocality' in types or 'neighborhood' in types:
                    components['suburb'] = long_name
                elif 'locality' in types or 'administrative_area_level_2' in types:
                    components['city'] = long_name
                elif 'administrative_area_level_1' in types:
                    components['state'] = short_name
                elif 'postal_code' in types:
                    components['postcode'] = long_name
                elif 'country' in types:
                    components['country'] = long_name
                    components['country_code'] = short_name
            
            return {
                'full_address': result.get('formatted_address', ''),
                'road': components.get('road', ''),
                'house_number': components.get('house_number', ''),
                'suburb': components.get('suburb', ''),
                'city': components.get('city', ''),
                'state': components.get('state', ''),
                'postcode': components.get('postcode', ''),
                'country': components.get('country', ''),
                'country_code': components.get('country_code', '').upper()
            }
                
        except Exception as e:
            logger.error(f"Google Maps detailed geocoding error: {str(e)}")
            return None
    
    def get_address_or_fallback(self, lat: float, lng: float, language: str = 'pt') -> str:
        """
        Get address with automatic fallback to coordinates if geocoding fails.
        
        Args:
            lat: Latitude
            lng: Longitude
            language: Language for the address (default: 'pt')
        
        Returns:
            Address string or formatted coordinates
        """
        address = self.reverse_geocode_full(lat, lng, language)
        if address:
            return address
        else:
            return f"{lat:.6f}, {lng:.6f}"

    @lru_cache(maxsize=1000)
    def reverse_geocode_full(self, lat: float, lng: float, language: str = 'pt') -> Optional[str]:
        """
        Convert coordinates to address with FULL street names (not abbreviated).
        
        Args:
            lat: Latitude
            lng: Longitude
            language: Language for the address (default: 'pt' for Portuguese)
        
        Returns:
            Full address string (unabbreviated) or None if geocoding fails
        """
        try:
            # Round coordinates to 4 decimal places for caching
            lat_rounded = round(lat, 4)
            lng_rounded = round(lng, 4)
            
            # Perform reverse geocoding
            results = self.client.reverse_geocode(
                (lat_rounded, lng_rounded),
                language=language
            )
            
            if not results or len(results) == 0:
                logger.warning(f"No address found for coordinates: {lat}, {lng}")
                return None
            
            # Extract components with long_name (full names)
            result = results[0]
            components = {}
            
            for component in result.get('address_components', []):
                types = component.get('types', [])
                long_name = component.get('long_name', '')
                
                if 'street_number' in types:
                    components['number'] = long_name
                elif 'route' in types:
                    components['street'] = long_name  # Nome completo da rua!
                elif 'sublocality' in types or 'sublocality_level_1' in types:
                    components['neighborhood'] = long_name
                elif 'administrative_area_level_2' in types:
                    components['city'] = long_name
                elif 'administrative_area_level_1' in types:
                    components['state'] = long_name  # Nome completo do estado
                elif 'postal_code' in types:
                    components['postal_code'] = long_name
                elif 'country' in types:
                    components['country'] = long_name
            
            # Construir endereço completo
            address_parts = []
            
            # Rua + Número
            if components.get('street'):
                street_part = components['street']
                if components.get('number'):
                    street_part += f", {components['number']}"
                address_parts.append(street_part)
            
            # Bairro
            if components.get('neighborhood'):
                address_parts.append(components['neighborhood'])
            
            # Cidade - Estado
            city_state = []
            if components.get('city'):
                city_state.append(components['city'])
            if components.get('state'):
                city_state.append(components['state'])
            if city_state:
                address_parts.append(' - '.join(city_state))
            
            # CEP
            if components.get('postal_code'):
                address_parts.append(components['postal_code'])
            
            # País
            if components.get('country'):
                address_parts.append(components['country'])
            
            # Juntar tudo
            full_address = ', '.join(address_parts)
            return full_address
                
        except Exception as e:
            logger.error(f"Google Maps geocoding error: {str(e)}")
            return None

# Singleton instances
_geocoding_service = None
_google_geocoding_service = None

def get_geocoding_service() -> GeocodingService:
    """Get or create the singleton Nominatim geocoding service instance."""
    global _geocoding_service
    if _geocoding_service is None:
        _geocoding_service = GeocodingService()
    return _geocoding_service

def get_google_geocoding_service() -> GoogleGeocodingService:
    """
    Get or create the singleton Google Maps geocoding service instance.
    
    Requires GOOGLE_MAPS_API_KEY environment variable.
    
    Returns:
        GoogleGeocodingService instance
    
    Raises:
        ValueError: If GOOGLE_MAPS_API_KEY is not set
        ImportError: If googlemaps package is not installed
    """
    global _google_geocoding_service
    if _google_geocoding_service is None:
        _google_geocoding_service = GoogleGeocodingService()
    return _google_geocoding_service
