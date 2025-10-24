# How to Use HCLI in Docker

This guide shows you how to run HCLI in a Docker container for automated workflows, CI/CD pipelines, or isolated environments.

## Problem Statement

You want to use HCLI in a Docker container to:

- Automate IDA Pro analysis workflows
- Run HCLI commands in CI/CD pipelines
- Isolate HCLI operations from your host system
- Deploy HCLI-based services

## Prerequisites

- Docker installed on your system
- Valid IDA Pro license
- HCLI API key (see [Authentication](../getting-started/authentication.md))

## Step-by-Step Guide

### 1. Basic Dockerfile

Create a `Dockerfile` for a minimal HCLI installation:

```dockerfile
FROM python:3.11-slim

# Install dependencies
RUN apt-get update && \
    apt-get install -y curl unzip && \
    rm -rf /var/lib/apt/lists/*

# Install HCLI
RUN curl -fsSL https://hcli.docs.hex-rays.com/install | sh

# Set environment variables
ENV PATH="/root/.local/bin:${PATH}"

# Verify installation
RUN hcli --version

WORKDIR /workspace

CMD ["hcli", "--help"]
```

Build the image:

```bash
docker build -t hcli:latest .
```

### 2. Passing API Keys Securely

**Option A: Environment Variables (Recommended)**

Pass the API key at runtime without embedding it in the image:

```bash
docker run --rm \
  -e HCLI_API_KEY="${HCLI_API_KEY}" \
  hcli:latest hcli whoami
```

**Option B: Docker Secrets (Docker Swarm)**

For production deployments using Docker Swarm:

```bash
# Create a secret
echo "your-api-key" | docker secret create hcli_api_key -

# Use in service definition
docker service create \
  --name hcli-service \
  --secret hcli_api_key \
  hcli:latest
```

In your entrypoint script:

```bash
#!/bin/bash
export HCLI_API_KEY=$(cat /run/secrets/hcli_api_key)
exec "$@"
```

**Option C: Volume Mount for Credentials**

Mount your local credentials directory:

```bash
docker run --rm \
  -v ~/.hcli:/root/.hcli:ro \
  hcli:latest hcli whoami
```

## Best Practices

1. **Never embed API keys in images**: Always pass them at runtime
2. **Use specific tags**: Pin HCLI and base image versions for reproducibility
4. **Disable updates**: Set `HCLI_DISABLE_UPDATES=true` in containers

## Reference

For more information on environment variables, see:

- [Environment Variables Reference](../reference/environment-variables.md)
- [Authentication](../getting-started/authentication.md)

## Example Use Cases

### Automated Plugin Testing

```bash
docker run --rm \
  -e HCLI_API_KEY="${HCLI_API_KEY}" \
  -v $(pwd)/my-plugin:/workspace/plugin \
  hcli:latest hcli plugin lint /workspace/plugin
```

### Batch File Sharing

```bash
docker run --rm \
  -e HCLI_API_KEY="${HCLI_API_KEY}" \
  -v $(pwd)/samples:/samples \
  hcli:latest bash -c '
    for file in /samples/*.idb; do
      hcli share put "$file" --acl private
    done
  '
```
