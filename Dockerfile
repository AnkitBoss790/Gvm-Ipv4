# ----------------------
# Dockerfile for DragonCloud VPS
# ----------------------

# Base image
FROM ubuntu:22.04

# Set environment
ENV DEBIAN_FRONTEND=noninteractive

# Install basic packages
RUN apt update && apt install -y \
    sudo \
    openssh-server \
    wget \
    curl \
    git \
    nano \
    vim \
    iproute2 \
    net-tools \
    htop \
    unzip \
    && rm -rf /var/lib/apt/lists/*

# Setup SSH
RUN mkdir -p /var/run/sshd \
    && echo 'root:root' | chpasswd \
    && sed -i 's/#PermitRootLogin prohibit-password/PermitRootLogin yes/' /etc/ssh/sshd_config

# Expose SSH port
EXPOSE 22

# Start SSH server
CMD ["/usr/sbin/sshd", "-D"]
