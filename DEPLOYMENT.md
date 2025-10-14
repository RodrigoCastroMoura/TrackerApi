# Guia de Deploy - DocSmart API

## 📋 Pré-requisitos

### Variáveis de Ambiente Obrigatórias

```bash
# Segurança (OBRIGATÓRIO)
FLASK_SECRET_KEY=<sua-chave-secreta-aqui>  # Gere com: python -c 'import secrets; print(secrets.token_hex(32))'

# Banco de Dados (OBRIGATÓRIO)
MONGODB_URI=<sua-uri-mongodb>

# Servidor
PORT=8000  # Opcional, padrão: 8000

# CORS (Opcional, padrão: *)
CORS_ORIGINS=https://seuapp.com,https://admin.seuapp.com

# Rate Limiting (Recomendado para produção)
RATELIMIT_STORAGE_URL=redis://localhost:6379  # Para produção com Redis
# ou
RATELIMIT_STORAGE_URL=memory://  # Para desenvolvimento (padrão)

# Firebase (Opcional - para upload de documentos)
FIREBASE_BUCKET_NAME=<seu-bucket-firebase>

# Email (Opcional - para recuperação de senha)
MAIL_SERVER=smtp.gmail.com
MAIL_PORT=587
MAIL_USE_TLS=True
MAIL_USERNAME=<seu-email>
MAIL_PASSWORD=<sua-senha-app>
MAIL_DEFAULT_SENDER=noreply@seuapp.com

# URLs da Aplicação (Opcional)
APP_URL=https://seuapp.com
APP_URL_RECOVERY=https://seuapp.com/reset-password
APP_URL_DOCUMENT_SIGNATURE=https://seuapp.com/sign-document
```

## 🚀 Instalação

### 1. Instalar Dependências

```bash
pip install -r requirements.txt
```

### 2. Configurar Variáveis de Ambiente

Crie um arquivo `.env`:

```bash
FLASK_SECRET_KEY=$(python -c 'import secrets; print(secrets.token_hex(32))')
MONGODB_URI=mongodb://localhost:27017/docsmart
# ... outras variáveis
```

### 3. Criar Primeiro Usuário Admin

**Modo Interativo:**
```bash
python bootstrap.py
```

**Modo CLI:**
```bash
python bootstrap.py --name "Admin User" --email admin@example.com --password "SenhaSegura123"
```

**Apenas Criar Permissões:**
```bash
python bootstrap.py --permissions-only
```

### 4. Executar em Desenvolvimento

```bash
# Com Flask (apenas desenvolvimento)
python main.py

# Com Gunicorn (recomendado)
gunicorn -c gunicorn_config.py wsgi:app
```

### 5. Executar em Produção

```bash
# Usando configuração padrão do Gunicorn
gunicorn -c gunicorn_config.py wsgi:app

# Com número específico de workers
GUNICORN_WORKERS=4 gunicorn -c gunicorn_config.py wsgi:app

# Com log level específico
LOG_LEVEL=warning gunicorn -c gunicorn_config.py wsgi:app
```

## 🔧 Configuração de Rate Limiting

### Desenvolvimento (Em Memória)

Para desenvolvimento, o padrão é usar armazenamento em memória:

```bash
# Não configure RATELIMIT_STORAGE_URL ou use:
RATELIMIT_STORAGE_URL=memory://
```

⚠️ **Aviso:** Rate limiting em memória não é recomendado para produção!

### Produção (Redis)

Para produção, configure Redis:

**1. Instalar Redis:**
```bash
# Ubuntu/Debian
sudo apt-get install redis-server

# macOS
brew install redis

# Docker
docker run -d -p 6379:6379 redis:alpine
```

**2. Configurar variável de ambiente:**
```bash
RATELIMIT_STORAGE_URL=redis://localhost:6379
```

**3. Com autenticação Redis:**
```bash
RATELIMIT_STORAGE_URL=redis://:senha@localhost:6379
```

**4. Redis Cloud/Remoto:**
```bash
RATELIMIT_STORAGE_URL=redis://usuario:senha@redis.exemplo.com:6379/0
```

### Produção (Memcached - Alternativa)

```bash
# Instalar Memcached
sudo apt-get install memcached

# Configurar
RATELIMIT_STORAGE_URL=memcached://localhost:11211
```

## 🐳 Deploy com Docker

### Dockerfile Exemplo

```dockerfile
FROM python:3.11-slim

WORKDIR /app

# Instalar dependências do sistema
RUN apt-get update && apt-get install -y \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copiar requirements e instalar
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiar código da aplicação
COPY . .

# Expor porta
EXPOSE 8000

# Comando para rodar
CMD ["gunicorn", "-c", "gunicorn_config.py", "wsgi:app"]
```

### docker-compose.yml Exemplo

```yaml
version: '3.8'

services:
  api:
    build: .
    ports:
      - "8000:8000"
    environment:
      - FLASK_SECRET_KEY=${FLASK_SECRET_KEY}
      - MONGODB_URI=mongodb://mongo:27017/docsmart
      - RATELIMIT_STORAGE_URL=redis://redis:6379
    depends_on:
      - mongo
      - redis
    restart: unless-stopped

  mongo:
    image: mongo:6
    volumes:
      - mongo_data:/data/db
    restart: unless-stopped

  redis:
    image: redis:alpine
    restart: unless-stopped

volumes:
  mongo_data:
```

**Executar:**
```bash
# Gerar SECRET_KEY
export FLASK_SECRET_KEY=$(python -c 'import secrets; print(secrets.token_hex(32))')

# Subir serviços
docker-compose up -d

# Criar admin (dentro do container)
docker-compose exec api python bootstrap.py
```

## 🔒 Checklist de Segurança

Antes de fazer deploy em produção:

- [ ] `FLASK_SECRET_KEY` configurada com valor seguro (não usar padrão)
- [ ] `MONGODB_URI` configurada e testada
- [ ] HTTPS configurado (certificado SSL)
- [ ] CORS configurado com origens específicas (não usar `*` em produção)
- [ ] Rate limiting com backend persistente (Redis/Memcached)
- [ ] Firewall configurado (permitir apenas portas necessárias)
- [ ] Logs configurados e monitorados
- [ ] Backup automático do banco de dados configurado
- [ ] Usuário admin criado com senha forte
- [ ] Variáveis de ambiente sensíveis não commitadas no Git

## 📊 Monitoramento

### Logs da Aplicação

Os logs são salvos em:
- Console (stdout/stderr)
- Arquivo `app.log` (rotação recomendada)

### Comandos Úteis

```bash
# Ver logs em tempo real
tail -f app.log

# Ver logs do Gunicorn
tail -f gunicorn_access.log
tail -f gunicorn_error.log

# Verificar status dos workers
ps aux | grep gunicorn

# Recarregar configuração (sem downtime)
kill -HUP $(cat gunicorn.pid)
```

## 🔄 Atualizações

Para atualizar a aplicação em produção:

```bash
# 1. Fazer pull das alterações
git pull origin main

# 2. Instalar novas dependências (se houver)
pip install -r requirements.txt

# 3. Recarregar Gunicorn (sem downtime)
kill -HUP $(cat gunicorn.pid)

# Ou reiniciar completamente
systemctl restart docsmart-api  # Se usando systemd
```

## 🐛 Troubleshooting

### Erro: "FLASK_SECRET_KEY must be set"

**Solução:** Configure a variável de ambiente:
```bash
export FLASK_SECRET_KEY=$(python -c 'import secrets; print(secrets.token_hex(32))')
```

### Erro: "Cannot create first admin user"

**Solução:** Use o script de bootstrap:
```bash
python bootstrap.py
```

### Aviso: "Rate limiting using in-memory storage"

**Solução:** Configure Redis em produção:
```bash
export RATELIMIT_STORAGE_URL=redis://localhost:6379
```

### Erro: "MongoDB connection failed"

**Solução:** Verifique:
1. MongoDB está rodando
2. URI está correta
3. Firewall permite conexão
4. Credenciais estão corretas

## 📞 Suporte

Para mais informações, consulte:
- [README.md](README.md) - Visão geral do projeto
- [PRODUCTION_READINESS_REPORT.md](PRODUCTION_READINESS_REPORT.md) - Análise de prontidão
