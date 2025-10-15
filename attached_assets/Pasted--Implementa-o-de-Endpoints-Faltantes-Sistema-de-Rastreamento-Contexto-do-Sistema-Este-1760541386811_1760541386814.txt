# 🚀 Implementação de Endpoints Faltantes - Sistema de Rastreamento

## 📋 Contexto do Sistema

Este é um portal de rastreamento veicular que se comunica com uma API externa em `https://tracker-api-rodrigocastrom1.replit.app`. O sistema já possui autenticação, CRUD de clientes/veículos/usuários e interfaces visuais prontas. Falta apenas a integração real dos endpoints de rastreamento para visualização no mapa.

---

## 🎯 ENDPOINTS A SEREM IMPLEMENTADOS

### 1️⃣ **RASTREAMENTO EM TEMPO REAL**

#### `GET /api/tracking/vehicles`
**Descrição:** Retorna lista de todos os veículos com última localização conhecida para visualização no mapa  
**Autenticação:** Bearer Token  
**Query Params:**
- `status` (opcional): "active" | "blocked" | "idle"
- `customer_id` (opcional): filtrar por cliente
- `page` (opcional): paginação
- `per_page` (opcional): itens por página

**Response Example:**
```json
{
  "vehicles": [
    {
      "id": "v1",
      "plate": "ABC-1234",
      "customer_id": "c1",
      "customer_name": "João Silva",
      "location": {
        "lat": -23.5505,
        "lng": -46.6333,
        "address": "Av. Paulista, 1000 - São Paulo, SP",
        "speed": 45,
        "heading": 180,
        "timestamp": "2024-10-15T14:30:00Z"
      },
      "status": "active",
      "tracker_serial": "TRK001",
      "is_tracking": true
    }
  ],
  "total": 10,
  "page": 1,
  "per_page": 20
}
```

---

#### `GET /api/tracking/vehicles/:id/location`
**Descrição:** Retorna a localização atual de um veículo específico  
**Autenticação:** Bearer Token  

**Response Example:**
```json
{
  "vehicle_id": "v1",
  "plate": "ABC-1234",
  "location": {
    "lat": -23.5505,
    "lng": -46.6333,
    "address": "Av. Paulista, 1000 - São Paulo, SP",
    "speed": 45,
    "heading": 180,
    "altitude": 750,
    "accuracy": 10,
    "timestamp": "2024-10-15T14:30:00Z"
  },
  "tracker": {
    "serial": "TRK001",
    "battery": 85,
    "signal_strength": 4,
    "online": true
  }
}
```

---

#### `GET /api/tracking/vehicles/:id/history`
**Descrição:** Retorna histórico de localizações do veículo para visualização de trajeto  
**Autenticação:** Bearer Token  
**Query Params:**
- `start_date` (obrigatório): data início (ISO 8601)
- `end_date` (obrigatório): data fim (ISO 8601)
- `interval` (opcional): "1min" | "5min" | "15min" | "1hour"

**Response Example:**
```json
{
  "vehicle_id": "v1",
  "plate": "ABC-1234",
  "period": {
    "start": "2024-10-15T00:00:00Z",
    "end": "2024-10-15T23:59:59Z"
  },
  "locations": [
    {
      "lat": -23.5505,
      "lng": -46.6333,
      "speed": 45,
      "heading": 180,
      "timestamp": "2024-10-15T14:30:00Z"
    },
    {
      "lat": -23.5510,
      "lng": -46.6340,
      "speed": 50,
      "heading": 180,
      "timestamp": "2024-10-15T14:35:00Z"
    }
  ],
  "total_distance": 125.5,
  "total_time_moving": 7200,
  "max_speed": 80,
  "avg_speed": 42
}
```

---

#### `GET /api/tracking/vehicles/:id/route`
**Descrição:** Retorna rota/trajeto otimizado do veículo para desenhar no mapa  
**Autenticação:** Bearer Token  
**Query Params:**
- `start_date` (obrigatório): data início
- `end_date` (obrigatório): data fim

**Response Example:**
```json
{
  "vehicle_id": "v1",
  "route": {
    "points": [
      [-23.5505, -46.6333],
      [-23.5510, -46.6340],
      [-23.5515, -46.6345]
    ],
    "polyline": "encoded_polyline_string",
    "total_distance": 125.5,
    "duration": 7200,
    "stops": [
      {
        "lat": -23.5505,
        "lng": -46.6333,
        "address": "Local X",
        "arrival": "2024-10-15T10:00:00Z",
        "departure": "2024-10-15T10:15:00Z",
        "duration": 900
      }
    ]
  }
}
```

---

### 2️⃣ **ALERTAS E NOTIFICAÇÕES**

#### `GET /api/alerts`
**Descrição:** Lista alertas configurados  
**Autenticação:** Bearer Token  

**Response Example:**
```json
{
  "alerts": [
    {
      "id": "a1",
      "type": "speed_limit",
      "vehicle_id": "v1",
      "vehicle_plate": "ABC-1234",
      "condition": {
        "max_speed": 80
      },
      "actions": ["email", "sms", "notification"],
      "active": true,
      "created_at": "2024-10-15T10:00:00Z"
    }
  ]
}
```

---

#### `POST /api/alerts`
**Descrição:** Cria novo alerta  
**Autenticação:** Bearer Token  

**Request Body:**
```json
{
  "type": "speed_limit",
  "vehicle_id": "v1",
  "condition": {
    "max_speed": 80
  },
  "actions": ["email", "notification"],
  "recipients": ["admin@example.com"]
}
```

**Tipos de Alerta:**
- `speed_limit` - Excesso de velocidade
- `geofence` - Cerca eletrônica
- `ignition` - Ignição ligada/desligada
- `low_battery` - Bateria baixa do rastreador
- `offline` - Rastreador offline
- `panic_button` - Botão de pânico

---

### 3️⃣ **RELATÓRIOS**

#### `GET /api/reports/vehicles/:id`
**Descrição:** Relatório de uso do veículo  
**Autenticação:** Bearer Token  
**Query Params:**
- `start_date` (obrigatório)
- `end_date` (obrigatório)
- `type`: "summary" | "detailed" | "stops" | "trips"

**Response Example:**
```json
{
  "vehicle_id": "v1",
  "plate": "ABC-1234",
  "period": {
    "start": "2024-10-01T00:00:00Z",
    "end": "2024-10-15T23:59:59Z"
  },
  "summary": {
    "total_distance": 1250.5,
    "total_time": 86400,
    "total_trips": 45,
    "total_stops": 120,
    "avg_speed": 42,
    "max_speed": 95,
    "fuel_consumption": 125.5
  },
  "trips": [
    {
      "start": {
        "timestamp": "2024-10-15T08:00:00Z",
        "location": "Origem A"
      },
      "end": {
        "timestamp": "2024-10-15T09:30:00Z",
        "location": "Destino B"
      },
      "distance": 45.5,
      "duration": 5400,
      "avg_speed": 30
    }
  ]
}
```

---

## 🛠️ IMPLEMENTAÇÃO NO BACKEND

### Passo 1: Adicionar métodos no `api-client.ts`

```typescript
// server/lib/api-client.ts

// Rastreamento em Tempo Real
async getVehicleLocations(params?: {
  status?: string;
  customer_id?: string;
  page?: number;
  per_page?: number;
}, token?: string) {
  const queryParams = new URLSearchParams();
  if (params) {
    Object.entries(params).forEach(([key, value]) => {
      if (value !== undefined) {
        queryParams.append(key, String(value));
      }
    });
  }
  const query = queryParams.toString();
  return this.get(`/api/tracking/vehicles${query ? `?${query}` : ""}`, token);
}

async getVehicleLocation(id: string, token?: string) {
  return this.get(`/api/tracking/vehicles/${id}/location`, token);
}

async getVehicleHistory(id: string, params: {
  start_date: string;
  end_date: string;
  interval?: string;
}, token?: string) {
  const queryParams = new URLSearchParams(params as any);
  return this.get(`/api/tracking/vehicles/${id}/history?${queryParams}`, token);
}

async getVehicleRoute(id: string, params: {
  start_date: string;
  end_date: string;
}, token?: string) {
  const queryParams = new URLSearchParams(params as any);
  return this.get(`/api/tracking/vehicles/${id}/route?${queryParams}`, token);
}

// Alertas
async getAlerts(token?: string) {
  return this.get("/api/alerts", token);
}

async createAlert(data: any, token?: string) {
  return this.post("/api/alerts", data, token);
}

async updateAlert(id: string, data: any, token?: string) {
  return this.put(`/api/alerts/${id}`, data, token);
}

async deleteAlert(id: string, token?: string) {
  return this.delete(`/api/alerts/${id}`, token);
}

// Relatórios
async getVehicleReport(id: string, params: {
  start_date: string;
  end_date: string;
  type?: string;
}, token?: string) {
  const queryParams = new URLSearchParams(params as any);
  return this.get(`/api/reports/vehicles/${id}?${queryParams}`, token);
}
```

### Passo 2: Adicionar rotas no `routes.ts`

```typescript
// server/routes.ts

// ========== ROTAS DE RASTREAMENTO ==========

app.get("/api/tracking/vehicles", requireAuth, async (req: Request, res: Response) => {
  try {
    const token = (req as any).token;
    const response = await apiClient.getVehicleLocations(req.query as any, token);
    
    if (response.error) {
      return res.status(response.status).json({ error: response.error });
    }
    
    res.json(response.data);
  } catch (error) {
    console.error("Get vehicle locations error:", error);
    res.status(500).json({ error: "Erro ao buscar localizações" });
  }
});

app.get("/api/tracking/vehicles/:id/location", requireAuth, async (req: Request, res: Response) => {
  try {
    const token = (req as any).token;
    const response = await apiClient.getVehicleLocation(req.params.id, token);
    
    if (response.error) {
      return res.status(response.status).json({ error: response.error });
    }
    
    res.json(response.data);
  } catch (error) {
    console.error("Get vehicle location error:", error);
    res.status(500).json({ error: "Erro ao buscar localização" });
  }
});

app.get("/api/tracking/vehicles/:id/history", requireAuth, async (req: Request, res: Response) => {
  try {
    const token = (req as any).token;
    const { start_date, end_date, interval } = req.query;
    
    if (!start_date || !end_date) {
      return res.status(400).json({ error: "start_date e end_date são obrigatórios" });
    }
    
    const response = await apiClient.getVehicleHistory(
      req.params.id,
      { start_date: start_date as string, end_date: end_date as string, interval: interval as string },
      token
    );
    
    if (response.error) {
      return res.status(response.status).json({ error: response.error });
    }
    
    res.json(response.data);
  } catch (error) {
    console.error("Get vehicle history error:", error);
    res.status(500).json({ error: "Erro ao buscar histórico" });
  }
});

app.get("/api/tracking/vehicles/:id/route", requireAuth, async (req: Request, res: Response) => {
  try {
    const token = (req as any).token;
    const { start_date, end_date } = req.query;
    
    if (!start_date || !end_date) {
      return res.status(400).json({ error: "start_date e end_date são obrigatórios" });
    }
    
    const response = await apiClient.getVehicleRoute(
      req.params.id,
      { start_date: start_date as string, end_date: end_date as string },
      token
    );
    
    if (response.error) {
      return res.status(response.status).json({ error: response.error });
    }
    
    res.json(response.data);
  } catch (error) {
    console.error("Get vehicle route error:", error);
    res.status(500).json({ error: "Erro ao buscar rota" });
  }
});

// ========== ROTAS DE ALERTAS ==========

app.get("/api/alerts", requireAuth, async (req: Request, res: Response) => {
  try {
    const token = (req as any).token;
    const response = await apiClient.getAlerts(token);
    
    if (response.error) {
      return res.status(response.status).json({ error: response.error });
    }
    
    res.json(response.data);
  } catch (error) {
    console.error("Get alerts error:", error);
    res.status(500).json({ error: "Erro ao buscar alertas" });
  }
});

app.post("/api/alerts", requireAuth, async (req: Request, res: Response) => {
  try {
    const token = (req as any).token;
    const response = await apiClient.createAlert(req.body, token);
    
    if (response.error) {
      return res.status(response.status).json({ error: response.error });
    }
    
    res.status(201).json(response.data);
  } catch (error) {
    console.error("Create alert error:", error);
    res.status(500).json({ error: "Erro ao criar alerta" });
  }
});

app.put("/api/alerts/:id", requireAuth, async (req: Request, res: Response) => {
  try {
    const token = (req as any).token;
    const response = await apiClient.updateAlert(req.params.id, req.body, token);
    
    if (response.error) {
      return res.status(response.status).json({ error: response.error });
    }
    
    res.json(response.data);
  } catch (error) {
    console.error("Update alert error:", error);
    res.status(500).json({ error: "Erro ao atualizar alerta" });
  }
});

app.delete("/api/alerts/:id", requireAuth, async (req: Request, res: Response) => {
  try {
    const token = (req as any).token;
    const response = await apiClient.deleteAlert(req.params.id, token);
    
    if (response.error) {
      return res.status(response.status).json({ error: response.error });
    }
    
    res.status(204).send();
  } catch (error) {
    console.error("Delete alert error:", error);
    res.status(500).json({ error: "Erro ao deletar alerta" });
  }
});

// ========== ROTAS DE RELATÓRIOS ==========

app.get("/api/reports/vehicles/:id", requireAuth, async (req: Request, res: Response) => {
  try {
    const token = (req as any).token;
    const { start_date, end_date, type } = req.query;
    
    if (!start_date || !end_date) {
      return res.status(400).json({ error: "start_date e end_date são obrigatórios" });
    }
    
    const response = await apiClient.getVehicleReport(
      req.params.id,
      { start_date: start_date as string, end_date: end_date as string, type: type as string },
      token
    );
    
    if (response.error) {
      return res.status(response.status).json({ error: response.error });
    }
    
    res.json(response.data);
  } catch (error) {
    console.error("Get vehicle report error:", error);
    res.status(500).json({ error: "Erro ao gerar relatório" });
  }
});
```

---

## 🎨 INTEGRAÇÃO NO FRONTEND

### Atualizar `Tracking.tsx` - Rastreamento em Tempo Real:

```typescript
import { useState } from "react";
import { useQuery, useMutation } from "@tanstack/react-query";
import { VehicleMap } from "@/components/VehicleMap";
import { apiRequest, queryClient } from "@/lib/queryClient";
import { useToast } from "@/hooks/use-toast";

export default function Tracking() {
  const { toast } = useToast();
  
  const { data: response, isLoading } = useQuery({
    queryKey: ["/api/tracking/vehicles"],
    refetchInterval: 5000, // Atualiza a cada 5 segundos
  });

  const lockMutation = useMutation({
    mutationFn: async (id: string) => {
      await apiRequest("POST", `/api/vehicles/${id}/block`);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["/api/tracking/vehicles"] });
      toast({
        title: "Veículo bloqueado",
        description: "Veículo bloqueado com sucesso!",
      });
    },
    onError: (error: Error) => {
      toast({
        title: "Erro ao bloquear veículo",
        description: error.message,
        variant: "destructive",
      });
    },
  });

  const unlockMutation = useMutation({
    mutationFn: async (id: string) => {
      await apiRequest("POST", `/api/vehicles/${id}/unblock`);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["/api/tracking/vehicles"] });
      toast({
        title: "Veículo desbloqueado",
        description: "Veículo desbloqueado com sucesso!",
      });
    },
    onError: (error: Error) => {
      toast({
        title: "Erro ao desbloquear veículo",
        description: error.message,
        variant: "destructive",
      });
    },
  });

  const vehicles = response?.vehicles?.map((v: any) => ({
    id: v.id,
    plate: v.plate,
    customerName: v.customer_name,
    lat: v.location?.lat || 0,
    lng: v.location?.lng || 0,
    status: v.status,
    speed: v.location?.speed || 0,
    lastUpdate: v.location?.timestamp ? new Date(v.location.timestamp).toLocaleString('pt-BR') : 'Sem dados',
  })) || [];

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-bold">Rastreamento em Tempo Real</h1>
        <p className="text-muted-foreground mt-1">Monitore a localização e status dos veículos</p>
      </div>

      {isLoading ? (
        <div className="text-center text-muted-foreground">Carregando localizações...</div>
      ) : (
        <VehicleMap
          vehicles={vehicles}
          onLockVehicle={(id) => lockMutation.mutate(id)}
          onUnlockVehicle={(id) => unlockMutation.mutate(id)}
        />
      )}
    </div>
  );
}
```

### Atualizar `TrackerTest.tsx` - Teste/Visualização de Rastreador:

```typescript
import { useState } from "react";
import { useQuery } from "@tantml/react-query";
import { VehicleMap } from "@/components/VehicleMap";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Search } from "lucide-react";

export default function TrackerTest() {
  const [vehicleId, setVehicleId] = useState("");
  const [selectedVehicleId, setSelectedVehicleId] = useState("");

  // Busca localização do veículo selecionado
  const { data: locationData, isLoading } = useQuery({
    queryKey: ["/api/tracking/vehicles", selectedVehicleId, "location"],
    queryFn: async () => {
      if (!selectedVehicleId) return null;
      const res = await fetch(`/api/tracking/vehicles/${selectedVehicleId}/location`, {
        headers: {
          Authorization: `Bearer ${localStorage.getItem("auth_token")}`,
        },
      });
      return res.json();
    },
    enabled: !!selectedVehicleId,
    refetchInterval: 3000, // Atualiza a cada 3 segundos
  });

  const handleSearch = () => {
    if (vehicleId.trim()) {
      setSelectedVehicleId(vehicleId.trim());
    }
  };

  const vehicle = locationData ? {
    id: locationData.vehicle_id,
    plate: locationData.plate,
    customerName: "Teste",
    lat: locationData.location?.lat || 0,
    lng: locationData.location?.lng || 0,
    status: "active" as const,
    speed: locationData.location?.speed || 0,
    lastUpdate: locationData.location?.timestamp 
      ? new Date(locationData.location.timestamp).toLocaleString('pt-BR') 
      : 'Sem dados',
  } : null;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-bold">Teste de Rastreador</h1>
        <p className="text-muted-foreground mt-1">Visualize a localização em tempo real de um veículo</p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Buscar Veículo</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="flex gap-4">
            <Input
              placeholder="ID do veículo"
              value={vehicleId}
              onChange={(e) => setVehicleId(e.target.value)}
              onKeyPress={(e) => e.key === 'Enter' && handleSearch()}
              data-testid="input-search-vehicle"
            />
            <Button 
              onClick={handleSearch} 
              data-testid="button-search-vehicle"
            >
              <Search className="h-4 w-4 mr-2" />
              Buscar
            </Button>
          </div>
        </CardContent>
      </Card>

      {isLoading && <div className="text-center text-muted-foreground">Carregando...</div>}

      {vehicle && (
        <VehicleMap
          vehicles={[vehicle]}
          onLockVehicle={() => {}}
          onUnlockVehicle={() => {}}
        />
      )}

      {!vehicle && selectedVehicleId && !isLoading && (
        <div className="text-center text-muted-foreground">Nenhum veículo encontrado</div>
      )}
    </div>
  );
}
```

---

## ✅ CHECKLIST DE IMPLEMENTAÇÃO

- [ ] Adicionar métodos de rastreamento no `api-client.ts`
- [ ] Adicionar rotas de rastreamento no `routes.ts`
- [ ] Adicionar métodos de alertas no `api-client.ts`
- [ ] Adicionar rotas de alertas no `routes.ts`
- [ ] Adicionar métodos de relatórios no `api-client.ts`
- [ ] Adicionar rotas de relatórios no `routes.ts`
- [ ] Integrar página de Rastreamento com API real
- [ ] Integrar página de Teste de Rastreador com API real (apenas visualização)
- [ ] Testar todos os endpoints
- [ ] Adicionar tratamento de erros
- [ ] Atualizar replit.md com novos endpoints

---

## 🚀 PRÓXIMOS PASSOS (OPCIONAIS)

1. **Implementar WebSocket** para atualizações em tempo real sem polling
2. **Adicionar mapas reais** (Leaflet, Mapbox ou Google Maps) substituindo o placeholder
3. **Criar sistema de notificações** push para alertas
4. **Implementar exportação de relatórios** (PDF/Excel)
5. **Adicionar analytics e dashboards** mais detalhados

---

## 📝 RESUMO

**Total de endpoints faltantes:** 9
- 4 endpoints de rastreamento (visualização)
- 4 endpoints de alertas
- 1 endpoint de relatórios

**Funcionalidade da página "Teste de Rastreador":**
- Apenas visualização de localização em tempo real no mapa
- Busca por ID de veículo
- Atualização automática a cada 3 segundos
- SEM envio de comandos ao rastreador
