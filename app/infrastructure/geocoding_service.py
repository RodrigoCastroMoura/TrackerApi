"""
Geocoding service using Nominatim (OpenStreetMap), Photon (komoot) and Google Maps
for reverse geocoding.

This service converts GPS coordinates (latitude, longitude) into human-readable addresses.
It implements rate limiting and caching for optimal performance.

Providers:
- Nominatim (OpenStreetMap): Free, rate-limited (1 req/sec)
- Photon (komoot): Free, no API key, used as secondary fallback
- Google Maps: Paid, requires GOOGLE_MAPS_API_KEY environment variable
"""

from geopy.geocoders import Nominatim, Photon
from geopy.exc import GeocoderTimedOut, GeocoderUnavailable
import logging
import time
import os
from functools import lru_cache
from typing import Optional, Dict
from app.domain.models import reverse_geocode as db_reverse_geocode

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

    def get_address(self, lat: float, lng: float) -> Optional[str]:
        """Address or None (no coordinate fallback) — used by FallbackGeocodingService."""
        return self.reverse_geocode(lat, lng)


class PhotonGeocodingService:
    """
    Service for reverse geocoding using Photon (komoot), built on OpenStreetMap data.

    Free, no API key required. Intended as a secondary fallback for when both
    Google Maps and Nominatim are unavailable or unstable.
    """

    # Photon's API only accepts these language codes (a 'pt' request 400s);
    # anything else is dropped so Photon falls back to the OSM tag's default
    # name, which for Brazil is already Portuguese.
    SUPPORTED_LANGUAGES = {'de', 'en', 'fr'}

    def __init__(self):
        self.geolocator = Photon(timeout=10)

    @lru_cache(maxsize=1000)
    def reverse_geocode(self, lat: float, lng: float, language: str = 'pt') -> Optional[str]:
        """
        Convert coordinates to address using Photon.

        Args:
            lat: Latitude
            lng: Longitude
            language: Language for the address (only 'de', 'en', 'fr' are
                sent to Photon; anything else uses the OSM default name)

        Returns:
            Address string or None if geocoding fails
        """
        try:
            lat_rounded = round(lat, 4)
            lng_rounded = round(lng, 4)

            kwargs = {}
            if language in self.SUPPORTED_LANGUAGES:
                kwargs['language'] = language

            location = self.geolocator.reverse(
                f"{lat_rounded}, {lng_rounded}",
                **kwargs
            )

            if location:
                return location.address
            else:
                logger.warning(f"No address found for coordinates: {lat}, {lng}")
                return None

        except GeocoderTimedOut:
            logger.error(f"Photon geocoding timeout for coordinates: {lat}, {lng}")
            return None
        except GeocoderUnavailable:
            logger.error("Photon service unavailable")
            return None
        except Exception as e:
            logger.error(f"Photon geocoding error: {str(e)}")
            return None

    @lru_cache(maxsize=1000)
    def reverse_geocode_detailed(self, lat: float, lng: float, language: str = 'pt') -> Optional[Dict]:
        """
        Convert coordinates to detailed address components using Photon.

        Args:
            lat: Latitude
            lng: Longitude
            language: Language for the address

        Returns:
            Dictionary with address components or None
        """
        try:
            lat_rounded = round(lat, 4)
            lng_rounded = round(lng, 4)

            kwargs = {}
            if language in self.SUPPORTED_LANGUAGES:
                kwargs['language'] = language

            location = self.geolocator.reverse(
                f"{lat_rounded}, {lng_rounded}",
                **kwargs
            )

            if location and location.raw:
                props = location.raw.get('properties', {})
                # Photon puts the street name in 'name' (not 'street') when
                # the result itself is a street/highway feature.
                road = props.get('street') or (
                    props.get('name', '') if props.get('osm_key') == 'highway' else ''
                )
                return {
                    'full_address': location.address,
                    'road': road,
                    'house_number': props.get('housenumber', ''),
                    'suburb': props.get('district', ''),
                    'city': props.get('city', ''),
                    'state': props.get('state', ''),
                    'postcode': props.get('postcode', ''),
                    'country': props.get('country', ''),
                    'country_code': props.get('countrycode', '').upper()
                }
            else:
                logger.warning(f"No detailed address found for coordinates: {lat}, {lng}")
                return None

        except GeocoderTimedOut:
            logger.error(f"Photon geocoding timeout for coordinates: {lat}, {lng}")
            return None
        except GeocoderUnavailable:
            logger.error("Photon service unavailable")
            return None
        except Exception as e:
            logger.error(f"Photon geocoding error: {str(e)}")
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
        return address if address else f"{lat:.6f}, {lng:.6f}"

    def get_address(self, lat: float, lng: float) -> Optional[str]:
        """Address or None (no coordinate fallback) — used by FallbackGeocodingService."""
        return self.reverse_geocode(lat, lng)


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

    def get_address(self, lat: float, lng: float) -> Optional[str]:
        """Address or None (no coordinate fallback)."""
        return self.reverse_geocode_full(lat, lng)


class DatabaseGeocodingService:
    """
    Service for reverse geocoding using the OSM data imported into the local
    database (Street/Address/Boundary/Neighbourhood collections).

    No external API calls, no rate limiting - just proximity/point-in-polygon
    queries against data already in MongoDB.
    """

    def reverse_geocode(self, lat: float, lng: float, max_distance_m: int = 200) -> Optional[str]:
        """Convert coordinates to address using the local database."""
        result = db_reverse_geocode(lat, lng, max_distance_m=max_distance_m)
        return result.get('endereco_completo')

    def reverse_geocode_detailed(self, lat: float, lng: float, max_distance_m: int = 200) -> Optional[Dict]:
        """Convert coordinates to detailed address components using the local database."""
        return db_reverse_geocode(lat, lng, max_distance_m=max_distance_m)

    def get_address_or_fallback(self, lat: float, lng: float) -> str:
        """Get address with automatic fallback to coordinates if geocoding fails."""
        address = self.reverse_geocode(lat, lng)
        return address if address else f"{lat:.6f}, {lng:.6f}"

    def get_address(self, lat: float, lng: float) -> Optional[str]:
        """Address or None (no coordinate fallback) — used by FallbackGeocodingService."""
        return self.reverse_geocode(lat, lng)


# Singleton instances
_geocoding_service = None
_google_geocoding_service = None
_photon_geocoding_service = None
_database_geocoding_service = None

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

def get_photon_geocoding_service() -> PhotonGeocodingService:
    """Get or create the singleton Photon geocoding service instance."""
    global _photon_geocoding_service
    if _photon_geocoding_service is None:
        _photon_geocoding_service = PhotonGeocodingService()
    return _photon_geocoding_service

def get_database_geocoding_service() -> DatabaseGeocodingService:
    """Get or create the singleton database (local OSM data) geocoding service instance."""
    global _database_geocoding_service
    if _database_geocoding_service is None:
        _database_geocoding_service = DatabaseGeocodingService()
    return _database_geocoding_service

# Maps GEOCODING_PROVIDER values to their singleton factory functions.
_GEOCODING_PROVIDER_FACTORIES = {
    'google': get_google_geocoding_service,
    'nominatim': get_geocoding_service,
    'photon': get_photon_geocoding_service,
    'database': get_database_geocoding_service,
}


def get_configured_geocoding_service():
    """
    Get the geocoding service selected via the GEOCODING_PROVIDER env var.

    Set GEOCODING_PROVIDER to 'google', 'nominatim' or 'photon' to choose the
    active provider (e.g. switch away from Google without touching code when
    there's no budget for it, or if it's having an outage). Defaults to
    'nominatim' (free, no API key) when unset.

    Returns:
        The selected geocoding service instance.

    Raises:
        ValueError: If GEOCODING_PROVIDER is 'google' but GOOGLE_MAPS_API_KEY
            is not configured, or if GEOCODING_PROVIDER is set to an unknown value.
    """
    provider = os.getenv('GEOCODING_PROVIDER', 'photon').strip().lower()
    factory = _GEOCODING_PROVIDER_FACTORIES.get(provider)
    if factory is None:
        raise ValueError(
            f"Unknown GEOCODING_PROVIDER '{provider}'. "
            f"Valid options: {', '.join(_GEOCODING_PROVIDER_FACTORIES)}"
        )
    return factory()
