#!/usr/bin/env python3
"""
Script de teste para os novos endpoints de customer
"""
import requests
import json

BASE_URL = "http://localhost:5000/api"

def test_customer_login():
    """Testa login do customer"""
    print("\n=== Testando Login do Customer ===")
    
    # Note: Para testar, você precisará de um customer válido no banco
    # Este é apenas um exemplo de estrutura
    login_data = {
        "identifier": "customer@example.com",  # ou CPF ou telefone
        "password": "senha123"
    }
    
    response = requests.post(f"{BASE_URL}/auth/customer/login", json=login_data)
    print(f"Status: {response.status_code}")
    if response.status_code == 200:
        data = response.json()
        print(f"✓ Login bem-sucedido")
        print(f"Access Token: {data.get('access_token')[:20]}...")
        print(f"Refresh Token: {data.get('refresh_token')[:20]}...")
        return data.get('access_token')
    else:
        print(f"✗ Erro: {response.json()}")
        return None

def test_customer_profile(token):
    """Testa obtenção do perfil do customer"""
    print("\n=== Testando Obtenção do Perfil ===")
    
    headers = {"Authorization": f"Bearer {token}"}
    response = requests.get(f"{BASE_URL}/auth/customer/profile", headers=headers)
    
    print(f"Status: {response.status_code}")
    if response.status_code == 200:
        data = response.json()
        print(f"✓ Perfil obtido com sucesso")
        print(f"Nome: {data.get('name')}")
        print(f"Email: {data.get('email')}")
        print(f"CPF: {data.get('document')}")
        return True
    else:
        print(f"✗ Erro: {response.json()}")
        return False

def test_update_profile(token):
    """Testa atualização do perfil do customer"""
    print("\n=== Testando Atualização do Perfil ===")
    
    update_data = {
        "phone": "11999999999",
        "city": "São Paulo"
    }
    
    headers = {"Authorization": f"Bearer {token}"}
    response = requests.put(f"{BASE_URL}/auth/customer/profile", json=update_data, headers=headers)
    
    print(f"Status: {response.status_code}")
    if response.status_code == 200:
        print(f"✓ Perfil atualizado com sucesso")
        return True
    else:
        print(f"✗ Erro: {response.json()}")
        return False

def test_customer_vehicles(token):
    """Testa listagem de veículos do customer"""
    print("\n=== Testando Listagem de Veículos ===")
    
    headers = {"Authorization": f"Bearer {token}"}
    response = requests.get(f"{BASE_URL}/auth/customer/vehicles", headers=headers)
    
    print(f"Status: {response.status_code}")
    if response.status_code == 200:
        data = response.json()
        print(f"✓ Veículos obtidos com sucesso")
        print(f"Total de veículos: {data.get('total')}")
        for vehicle in data.get('vehicles', [])[:3]:  # Mostra apenas os 3 primeiros
            print(f"  - {vehicle.get('dsmodelo')} ({vehicle.get('dsplaca')})")
        return True
    else:
        print(f"✗ Erro: {response.json()}")
        return False

def test_refresh_token(refresh_token):
    """Testa refresh token para customer"""
    print("\n=== Testando Refresh Token ===")
    
    headers = {"Authorization": f"Bearer {refresh_token}"}
    response = requests.post(f"{BASE_URL}/auth/refresh", headers=headers)
    
    print(f"Status: {response.status_code}")
    if response.status_code == 200:
        data = response.json()
        print(f"✓ Token renovado com sucesso")
        print(f"Novo Access Token: {data.get('access_token')[:20]}...")
        return True
    else:
        print(f"✗ Erro: {response.json()}")
        return False

def check_swagger_docs():
    """Verifica se os novos endpoints aparecem na documentação Swagger"""
    print("\n=== Verificando Documentação Swagger ===")
    
    response = requests.get(f"{BASE_URL.replace('/api', '')}/swagger.json")
    
    if response.status_code == 200:
        swagger = response.json()
        paths = swagger.get('paths', {})
        
        # Verificar se os novos endpoints estão documentados
        customer_endpoints = [
            '/api/auth/customer/profile',
            '/api/auth/customer/vehicles',
            '/api/auth/customer/vehicles/{id}'
        ]
        
        print("Endpoints de customer na documentação:")
        for endpoint in customer_endpoints:
            if endpoint in paths:
                print(f"✓ {endpoint}")
            else:
                print(f"✗ {endpoint} não encontrado")
        
        return True
    else:
        print(f"✗ Erro ao obter Swagger: {response.status_code}")
        return False

if __name__ == "__main__":
    print("=" * 60)
    print("TESTE DOS NOVOS ENDPOINTS DE CUSTOMER")
    print("=" * 60)
    
    # Verificar documentação Swagger
    check_swagger_docs()
    
    print("\n" + "=" * 60)
    print("NOTA: Para testar os endpoints autenticados,")
    print("você precisa ter um customer válido no banco de dados.")
    print("=" * 60)
    
    # Exemplo de fluxo completo (comentado por falta de dados reais)
    """
    token = test_customer_login()
    if token:
        test_customer_profile(token)
        test_update_profile(token)
        test_customer_vehicles(token)
        # test_refresh_token seria testado com refresh_token do login
    """
