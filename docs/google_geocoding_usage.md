# Google Maps Geocoding - Guia de Uso

## Visão Geral

Este documento explica como usar o serviço de geocodificação reversa do Google Maps integrado ao sistema.

## Configuração

### 1. Obter API Key do Google Maps

1. Acesse o [Google Cloud Console](https://console.cloud.google.com/)
2. Crie um novo projeto ou selecione um existente
3. Ative a **Google Maps Geocoding API**
4. Crie uma API key em "Credenciais"
5. (Recomendado) Restrinja a API key para usar apenas Geocoding API

### 2. Configurar a API Key no Replit

No Replit, você deve armazenar a API key como um **Secret** (variável de ambiente criptografada):

1. Abra o painel **Secrets** no Replit (Tools > Secrets)
2. Clique em **New Secret**
3. Configure:
   - **Key**: `GOOGLE_MAPS_API_KEY`
   - **Value**: Sua API key do Google Maps
4. Clique em **Add Secret**

A API key ficará disponível como variável de ambiente para sua aplicação.

## Uso no Código

### Importar o Serviço

```python
from app.infrastructure.geocoding_service import get_google_geocoding_service
```

### Exemplo Básico: Buscar Endereço

```python
# Obter instância do serviço
geocoding_service = get_google_geocoding_service()

# Coordenadas de exemplo (São Paulo)
latitude = -23.5505
longitude = -46.6333

# Buscar endereço com fallback automático
address = geocoding_service.get_address_or_fallback(latitude, longitude)
print(f"Endereço: {address}")
# Saída: Praça da Sé - Sé, São Paulo - SP, Brasil
```

### Exemplo Avançado: Buscar Endereço Detalhado

```python
# Buscar componentes detalhados do endereço
details = geocoding_service.reverse_geocode_detailed(
    lat=-23.5505,
    lng=-46.6333,
    language='pt'
)

if details:
    print(f"Endereço completo: {details['full_address']}")
    print(f"Rua: {details['road']}")
    print(f"Número: {details['house_number']}")
    print(f"Bairro: {details['suburb']}")
    print(f"Cidade: {details['city']}")
    print(f"Estado: {details['state']}")
    print(f"CEP: {details['postcode']}")
    print(f"País: {details['country']}")
```

### Exemplo: Uso em Rotas de Tracking

```python
from app.infrastructure.geocoding_service import get_google_geocoding_service

@vehicle_tracking_ns.route('/last-location/<string:vehicle_id>')
class VehicleLastLocation(Resource):
    def get(self, vehicle_id):
        # ... buscar dados do veículo ...
        
        # Usar Google Maps para geocodificação
        geocoding_service = get_google_geocoding_service()
        address = geocoding_service.get_address_or_fallback(
            lat=vehicle_data.latitude,
            lng=vehicle_data.longitude
        )
        
        return {
            'latitude': vehicle_data.latitude,
            'longitude': vehicle_data.longitude,
            'address': address,
            'timestamp': vehicle_data.timestamp.isoformat()
        }
```

## Comparação: Nominatim vs Google Maps

| Característica | Nominatim (OpenStreetMap) | Google Maps |
|----------------|---------------------------|-------------|
| **Custo** | Gratuito | Pago ($5/1000 req após quota grátis) |
| **Limite de Taxa** | 1 req/segundo | ~50 req/segundo |
| **Qualidade** | Boa, mas variável | Excelente, consistente |
| **Cobertura Brasil** | Boa | Excelente |
| **Configuração** | Nenhuma | Requer API key |
| **Uso Comercial** | Permitido com atribuição | Permitido |

## Alternar Entre Provedores

### Usar Nominatim (Gratuito)
```python
from app.infrastructure.geocoding_service import get_geocoding_service

service = get_geocoding_service()
address = service.get_address_or_fallback(lat, lng)
```

### Usar Google Maps (Pago, Melhor Qualidade)
```python
from app.infrastructure.geocoding_service import get_google_geocoding_service

service = get_google_geocoding_service()
address = service.get_address_or_fallback(lat, lng)
```

## Tratamento de Erros

### API Key Não Configurada

```python
try:
    service = get_google_geocoding_service()
except ValueError as e:
    print(f"Erro: {e}")
    # Usar Nominatim como fallback
    service = get_geocoding_service()
```

### Endereço Não Encontrado

```python
address = service.reverse_geocode(lat, lng)

if address:
    print(f"Endereço: {address}")
else:
    print("Endereço não encontrado, exibindo coordenadas:")
    print(f"{lat:.6f}, {lng:.6f}")
```

## Cache e Performance

Ambos os serviços (Nominatim e Google Maps) implementam:

- **Cache LRU**: 1000 endereços em memória
- **Arredondamento**: Coordenadas arredondadas para 4 casas decimais (~11m precisão)
- **Tratamento de Erros**: Fallback automático para coordenadas

## Custos e Quota do Google Maps

### Quota Gratuita
- **$200 USD/mês** em créditos gratuitos
- ≈ **40.000 requisições gratuitas/mês** (após $200 de crédito)

### Preços (após quota gratuita)
- **Geocoding API**: $5.00 por 1.000 requisições
- **Geocoding API (volumes altos)**: Até $4.00 por 1.000 req

### Monitoramento
- Monitore o uso no [Google Cloud Console](https://console.cloud.google.com/apis/dashboard)
- Configure alertas de faturamento para evitar surpresas

## Segurança

### ✅ Boas Práticas

1. **Nunca** exponha sua API key no código-fonte
2. **Sempre** use Secrets do Replit para armazenar a chave
3. **Configure** restrições de API no Google Cloud:
   - Restringir por endereço IP (produção)
   - Restringir por domínio (aplicação web)
   - Restringir APIs permitidas (apenas Geocoding)

### ❌ Evite

```python
# NUNCA faça isso:
api_key = "AIzaSyABCDEF1234567890GHIJKLMNOPQRSTUV"
```

### ✅ Forma Correta

```python
import os

api_key = os.getenv('GOOGLE_MAPS_API_KEY')
if not api_key:
    raise ValueError("GOOGLE_MAPS_API_KEY não configurada")
```

## Referências

- [Google Maps Geocoding API Docs](https://developers.google.com/maps/documentation/geocoding)
- [Google Cloud Console](https://console.cloud.google.com/)
- [Pricing Calculator](https://mapsplatform.google.com/pricing/)
