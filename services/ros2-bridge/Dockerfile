FROM ros:jazzy-ros-base

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    ROS_DOMAIN_ID=0

# Install Python dependencies.
# kuksa-client is not bundled in the ROS base image, so we install it via pip.
# rclpy and std_msgs are already provided by ros:jazzy-ros-base.
# --break-system-packages is required because this image uses a system-managed Python.
COPY requirements.txt .
RUN pip install --no-cache-dir --break-system-packages -r requirements.txt

COPY main.py .

# ROS2 requires environment variables set by setup.bash (AMENT_PREFIX_PATH,
# PYTHONPATH, etc.) before any rclpy import can succeed.
# Sourcing inside the CMD is the standard pattern for ROS2 Docker containers.
CMD ["/bin/bash", "-c", "source /opt/ros/jazzy/setup.bash && python3 main.py"]
