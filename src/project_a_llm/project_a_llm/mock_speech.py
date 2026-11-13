"""Forward web test utterances to the normal speech input topic."""
import json

import rclpy
from rclpy.node import Node
from std_msgs.msg import String


class MockSpeech(Node):
    def __init__(self):
        super().__init__("workbench_mock_speech")
        self.output = self.create_publisher(String, "/project_a/agent/input", 10)
        self.create_subscription(String, "/project_a/test/utterance", self.forward, 10)

    def forward(self, msg):
        try:
            event = json.loads(msg.data)
            if not all(isinstance(event[k], str) and event[k].strip() for k in ("utterance_id", "text")):
                raise ValueError("缺少發話 ID 或文字")
        except (ValueError, KeyError, TypeError) as error:
            self.get_logger().warning(str(error))
            return
        self.output.publish(msg)
        self.get_logger().info(f"Published {event['utterance_id']}")


def main():
    rclpy.init()
    node = MockSpeech()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()
