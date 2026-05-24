FROM ros:jazzy-ros-base

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    ROS_DOMAIN_ID=0

# No additional pip dependencies — rclpy and std_msgs are pre-installed
# in ros:jazzy-ros-base. requirements.txt is kept for structural consistency.
COPY requirements.txt .

COPY main.py .

CMD ["/bin/bash", "-c", "source /opt/ros/jazzy/setup.bash && python3 main.py"]
