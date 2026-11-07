# syntax=docker/dockerfile:1
# Multi-stage Dockerfile for IDA Pro with idalib
# Builds for the build platform (linux/amd64 or linux/arm64) unless --platform is given
FROM python:3.13-slim AS base

# Install system dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install hcli standalone binary
FROM base AS hcli-installer
RUN curl -LsSf https://hcli.docs.hex-rays.com/install | sh

# Install IDA Pro using hcli
FROM base AS ida-installer

# Copy hcli binary from previous stage
COPY --from=hcli-installer /root/.local/bin/hcli /usr/local/bin/hcli

# Build arguments for IDA installation
ARG IDA_DOWNLOAD_ID=ida-pro:latest
ARG IDA_INSTALL_DIR=/opt/ida

# Install IDA Pro and create the Python environment.
# The API key and license ID come from the hcli_api_key and ida_license_id
# build secrets, so they are not stored in the build history, cache, or image
# layers.
RUN --mount=type=secret,id=hcli_api_key \
    --mount=type=secret,id=ida_license_id \
    if [ ! -s /run/secrets/hcli_api_key ]; then echo "Error: hcli_api_key build secret is required" && exit 1; fi && \
    if [ ! -s /run/secrets/ida_license_id ]; then echo "Error: ida_license_id build secret is required" && exit 1; fi && \
    HCLI_API_KEY="$(cat /run/secrets/hcli_api_key)" \
    hcli --disable-updates ida install \
    --download-id "${IDA_DOWNLOAD_ID}" \
    --license-id "$(cat /run/secrets/ida_license_id)" \
    --install-dir ${IDA_INSTALL_DIR} \
    --create-python-environment \
    --yes && \
    mkdir -p /root/.idapro && \
    find /tmp -name "*.hexlic" -exec cp {} /root/.idapro/ \; && \
    rm -rf /tmp/* /root/.cache/*

# Final stage
FROM base AS final

# Install IDA Pro from installer stage
ARG IDA_INSTALL_DIR=/opt/ida
COPY --from=ida-installer ${IDA_INSTALL_DIR} ${IDA_INSTALL_DIR}
COPY --from=ida-installer /root/.idapro /root/.idapro

# Set environment variables for IDA
ENV PATH="${IDA_INSTALL_DIR}:${PATH}"
ENV IDADIR="${IDA_INSTALL_DIR}"
ENV IDAPYTHON_VENV_EXECUTABLE="/root/.idapro/venv/bin/python"
ENV TVHEADLESS=1

# Install Python dependencies into the IDA venv
RUN /root/.idapro/venv/bin/python -m pip install --no-cache-dir \
    idapro \
    ida-domain

# Create working directory for analysis
WORKDIR /analysis

# Copy the entrypoint script
COPY entrypoint.py /usr/local/bin/entrypoint.py
RUN chmod +x /usr/local/bin/entrypoint.py

# Set entrypoint
ENTRYPOINT ["/root/.idapro/venv/bin/python", "/usr/local/bin/entrypoint.py"]

# Default: show help if no arguments provided
CMD ["--help"]
