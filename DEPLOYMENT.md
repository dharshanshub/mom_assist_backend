# Deployment — MoM Assist (Azure Container Apps)

CI/CD: push to `main` → GitHub Actions builds the Docker image → pushes to
Docker Hub (`:latest` + commit SHA) → `az containerapp update` rolls out a new
revision on Azure Container Apps.

| Component | Repo | Image | Container App | Port |
|-----------|------|-------|---------------|------|
| Backend (FastAPI) | `mom_assist_backend` | `dharshanscientist/mom-assist-backend` | `mom-assist-backend` | 8000 |
| Frontend (nginx)  | `mom_assist_frontend` | `dharshanscientist/mom-assist-frontend` | `mom-assist-frontend` | 8080 |

- **Resource group:** `rg_talent_search` (existing)
- **Container Apps environment:** `mom-assist-env` (new)
- **Region:** `eastus` (match your resource group's region)

---

## 1. GitHub Actions secrets

Add in **both** repos → Settings → Secrets and variables → Actions:

| Secret | Value |
|--------|-------|
| `DOCKERHUB_USERNAME` | `dharshanscientist` |
| `DOCKERHUB_TOKEN` | Docker Hub access token (Account → Security → New Access Token) |
| `AZURE_CREDENTIALS` | Service-principal JSON (see §2) — can reuse the Talent AI one |

**Frontend repo only:**

| Secret | Value |
|--------|-------|
| `VITE_API_BASE_URL` | `https://<backend-fqdn>` (filled after §3) |

---

## 2. Azure service principal (for `AZURE_CREDENTIALS`)

The existing Talent AI service principal is already scoped to `rg_talent_search`,
so you can reuse the same `AZURE_CREDENTIALS` JSON. To create a fresh one:

```bash
az ad sp create-for-rbac --name "mom-assist-gh" --role contributor \
  --scopes /subscriptions/<SUB_ID>/resourceGroups/rg_talent_search \
  --sdk-auth
```

Paste the entire JSON output into the `AZURE_CREDENTIALS` secret.

---

## 3. One-time provisioning

```bash
RG=rg_talent_search
LOC=eastus
ENVNAME=mom-assist-env
DH=dharshanscientist

# Container Apps environment (new, inside the existing RG)
az containerapp env create -n $ENVNAME -g $RG -l $LOC

# Seed the first images so 'create' has something to pull
docker build -t $DH/mom-assist-backend:latest ./backend  && docker push $DH/mom-assist-backend:latest
docker build -t $DH/mom-assist-frontend:latest ./frontend && docker push $DH/mom-assist-frontend:latest

# ── Backend ───────────────────────────────────────────────────────────────────
# Sensitive values are stored as Container App *secrets* and referenced via secretref:
az containerapp create -n mom-assist-backend -g $RG --environment $ENVNAME \
  --image $DH/mom-assist-backend:latest --target-port 8000 --ingress external \
  --min-replicas 1 --max-replicas 3 \
  --secrets \
      openai-key=<OPENAI_API_KEY> \
      pinecone-key=<PINECONE_API_KEY> \
      azure-conn="<AZURE_STORAGE_CONNECTION_STRING>" \
      jwt-secret=<JWT_SECRET_KEY> \
  --env-vars \
      APP_ENV=prod LOG_LEVEL=INFO \
      OPENAI_API_KEY=secretref:openai-key \
      OPENAI_EMBEDDING_MODEL=text-embedding-3-small \
      OPENAI_LLM_MODEL=gpt-4o-mini \
      PINECONE_API_KEY=secretref:pinecone-key \
      PINECONE_INDEX_NAME=mom-rag PINECONE_CLOUD=aws PINECONE_REGION=us-east-1 \
      EMBEDDING_DIM=1536 TOP_K=5 \
      AZURE_STORAGE_CONNECTION_STRING=secretref:azure-conn \
      AZURE_STORAGE_CONTAINER=mom-rag-documents \
      JWT_SECRET_KEY=secretref:jwt-secret \
      CORS_ORIGINS=https://placeholder

BACKEND_URL=$(az containerapp show -n mom-assist-backend -g $RG \
  --query properties.configuration.ingress.fqdn -o tsv)
echo "Backend: https://$BACKEND_URL"

# ── Frontend ──────────────────────────────────────────────────────────────────
az containerapp create -n mom-assist-frontend -g $RG --environment $ENVNAME \
  --image $DH/mom-assist-frontend:latest --target-port 8080 --ingress external \
  --min-replicas 1 --max-replicas 2

FRONTEND_URL=$(az containerapp show -n mom-assist-frontend -g $RG \
  --query properties.configuration.ingress.fqdn -o tsv)
echo "Frontend: https://$FRONTEND_URL"

# Close the loop: allow the frontend origin through backend CORS
az containerapp update -n mom-assist-backend -g $RG \
  --set-env-vars CORS_ORIGINS=https://$FRONTEND_URL
```

> **Private Docker Hub repos?** Add to each `containerapp create`:
> `--registry-server docker.io --registry-username $DH --registry-password <DOCKERHUB_TOKEN>`

---

## 4. Finish wiring the frontend

1. Set the frontend repo secret `VITE_API_BASE_URL = https://<BACKEND_URL>`.
2. Actions tab → **Deploy Frontend** → *Run workflow*, so the bundle is rebuilt
   with the real backend URL baked in.

---

## 5. Day-2: how deploys work

- Every push to `main` (or a manual *Run workflow*) builds the image, pushes it to
  Docker Hub tagged with the commit SHA, and updates the Container App to that tag.
- **Changing env vars / secrets** is done out-of-band, e.g.:
  ```bash
  az containerapp secret set    -n mom-assist-backend -g rg_talent_search --secrets openai-key=<NEW>
  az containerapp update        -n mom-assist-backend -g rg_talent_search --set-env-vars LOG_LEVEL=DEBUG
  ```
- **Logs:** `az containerapp logs show -n mom-assist-backend -g rg_talent_search --follow`

## Security notes

- No secrets live in git — `.env` is git-ignored; only `.env.example` (placeholders) is committed.
- Runtime secrets are Azure Container App **secrets** referenced via `secretref:`, not plain env vars.
- Rotate the OpenAI / Pinecone / Azure Storage / JWT values if they were ever exposed locally.
