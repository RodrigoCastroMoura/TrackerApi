#!/usr/bin/env python3
"""
Exemplo de uso do serviço de geocodificação do Google Maps.

Este script demonstra como usar a API do Google Maps para converter
coordenadas GPS em endereços legíveis.

Pré-requisitos:
1. Configure a variável de ambiente GOOGLE_MAPS_API_KEY no Replit Secrets
2. Execute: python examples/test_google_geocoding.py
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.infrastructure.geocoding_service import (
    get_google_geocoding_service,
    get_geocoding_service
)


def test_google_geocoding():
    """Testa o serviço de geocodificação do Google Maps."""
    
    print("="*60)
    print("TESTE: Google Maps Geocoding API")
    print("="*60)
    
    # Verificar se API key está configurada
    api_key = os.getenv('GOOGLE_MAPS_API_KEY')
    if not api_key:
        print("\n❌ ERRO: GOOGLE_MAPS_API_KEY não está configurada!")
        print("\nPara configurar:")
        print("1. Acesse Tools > Secrets no Replit")
        print("2. Adicione uma nova secret:")
        print("   Key: GOOGLE_MAPS_API_KEY")
        print("   Value: Sua API key do Google Maps")
        print("\n⚠️  Usando Nominatim (OpenStreetMap) como fallback...\n")
        
        # Fallback para Nominatim
        service = get_geocoding_service()
        provider = "Nominatim (OpenStreetMap)"
    else:
        try:
            service = get_google_geocoding_service()
            provider = "Google Maps"
            print(f"\n✅ API key configurada: {api_key[:10]}...{api_key[-4:]}")
        except Exception as e:
            print(f"\n❌ Erro ao inicializar Google Maps: {e}")
            print("⚠️  Usando Nominatim como fallback...\n")
            service = get_geocoding_service()
            provider = "Nominatim (OpenStreetMap)"
    
    print(f"\nProvedor: {provider}")
    print("="*60)
    
    # Coordenadas de teste (locais famosos no Brasil)
    test_locations = [
        {
            'name': 'Praça da Sé, São Paulo',
            'lat': -23.5505,
            'lng': -46.6333
        },
        {
            'name': 'Cristo Redentor, Rio de Janeiro',
            'lat': -22.9519,
            'lng': -43.2105
        },
        {
            'name': 'Congresso Nacional, Brasília',
            'lat': -15.7998,
            'lng': -47.8645
        },
        {
            'name': 'Arena Corinthians, São Paulo',
            'lat': -23.5450,
            'lng': -46.4730
        }
    ]
    
    print("\n" + "="*60)
    print("TESTE 1: Busca Simples de Endereço")
    print("="*60)
    
    for location in test_locations:
        print(f"\n📍 {location['name']}")
        print(f"   Coordenadas: ({location['lat']}, {location['lng']})")
        
        address = service.get_address_or_fallback(
            lat=location['lat'],
            lng=location['lng']
        )
        
        print(f"   ➡️  Endereço: {address}")
    
    print("\n" + "="*60)
    print("TESTE 2: Busca Detalhada de Endereço")
    print("="*60)
    
    # Testar com a primeira localização
    location = test_locations[0]
    print(f"\n📍 {location['name']}")
    print(f"   Coordenadas: ({location['lat']}, {location['lng']})")
    
    details = service.reverse_geocode_detailed(
        lat=location['lat'],
        lng=location['lng'],
        language='pt'
    )
    
    if details:
        print("\n   Componentes do endereço:")
        print(f"   - Endereço completo: {details['full_address']}")
        print(f"   - Rua: {details['road']}")
        print(f"   - Número: {details['house_number'] or 'N/A'}")
        print(f"   - Bairro: {details['suburb']}")
        print(f"   - Cidade: {details['city']}")
        print(f"   - Estado: {details['state']}")
        print(f"   - CEP: {details['postcode']}")
        print(f"   - País: {details['country']} ({details['country_code']})")
    else:
        print("   ❌ Não foi possível obter detalhes do endereço")
    
    print("\n" + "="*60)
    print("TESTE 3: Teste de Fallback (Coordenadas Inválidas)")
    print("="*60)
    
    # Coordenadas no meio do oceano (sem endereço)
    print(f"\n📍 Oceano Atlântico")
    print(f"   Coordenadas: (0.0, -30.0)")
    
    address = service.get_address_or_fallback(lat=0.0, lng=-30.0)
    print(f"   ➡️  Resultado: {address}")
    
    print("\n" + "="*60)
    print("✅ TESTES CONCLUÍDOS!")
    print("="*60)
    
    print(f"\nProvedor usado: {provider}")
    if provider == "Google Maps":
        print("✅ Google Maps funcionando corretamente!")
    else:
        print("⚠️  Configure GOOGLE_MAPS_API_KEY para usar Google Maps")
    
    print()


if __name__ == '__main__':
    test_google_geocoding()
