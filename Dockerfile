# Use an official Ubuntu runtime as a parent image
FROM ubuntu:20.04

# Set the working directory in the container
WORKDIR /app

# Install necessary packages and dependencies
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y \
    python3-pip \
    git \
    ffmpeg \
    libffi-dev \
    libnacl-dev \
    libssl-dev \
    tzdata \
    && rm -rf /var/lib/apt/lists/*

# Copy the current directory contents into the container at /app
COPY . /app

# Install Python packages specified in requirements.txt
RUN pip3 install --no-cache-dir -r requirements.txt

# Install beets and plugins
RUN pip3 install beets beets-copyartifacts3

# Install beets-audible from GitHub
RUN git clone https://github.com/Neurrone/beets-audible.git /tmp/beets-audible && \
    cd /tmp/beets-audible && \
    python3 setup.py install && \
    rm -rf /tmp/beets-audible

# Make port 9977 available to the world outside this container
EXPOSE 9977

# Run app.py when the container launches
CMD ["python3", "app.py"]
EOF
