# Multi-stage Dockerfile for IDA Pro with idalib
# Force x86_64 architecture since hcli doesn't support Linux ARM64
FROM --platform=linux/amd64 python:3.13-slim AS base

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
ARG HCLI_API_KEY
ARG IDA_LICENSE_ID
ARG IDA_DOWNLOAD_ID=release/9.2/ida-pro/ida-pro_92_x64linux.run
ARG IDA_INSTALL_DIR=/opt/ida

# Validate required build arguments
RUN if [ -z "$HCLI_API_KEY" ]; then echo "Error: HCLI_API_KEY build argument is required" && exit 1; fi
RUN if [ -z "$IDA_LICENSE_ID" ]; then echo "Error: IDA_LICENSE_ID build argument is required" && exit 1; fi

# Install IDA Pro
ENV HCLI_API_KEY=${HCLI_API_KEY}
RUN hcli --disable-updates ida install \
    --download-id ${IDA_DOWNLOAD_ID} \
    --license-id ${IDA_LICENSE_ID} \
    --install-dir ${IDA_INSTALL_DIR} \
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
ENV TVHEADLESS=1

# Install Python dependencies
RUN pip install --no-cache-dir \
    idapro \
    ida-domain

# Create working directory for analysis
WORKDIR /analysis

# Copy the entrypoint script
COPY entrypoint.py /usr/local/bin/entrypoint.py
RUN chmod +x /usr/local/bin/entrypoint.py

# Set entrypoint
ENTRYPOINT ["python", "/usr/local/bin/entrypoint.py"]

# Default: show help if no arguments provided
CMD ["--help"]
