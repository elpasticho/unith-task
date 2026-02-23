#!/usr/bin/env python
"""CLI: publish N events to RabbitMQ, optionally with duplicates."""
from __future__ import annotations

import argparse
import asyncio
import json
import random
import string
import uuid

import aio_pika


async def publish(
    rabbitmq_url: str,
    exchange_name: str,
    queue_name: str,
    count: int,
    duplicates: bool,
    event_type: str,
) -> None:
    connection = await aio_pika.connect_robust(rabbitmq_url)
    async with connection:
        channel = await connection.channel()
        exchange = await channel.declare_exchange(
            exchange_name, aio_pika.ExchangeType.DIRECT, durable=True
        )
        await channel.declare_queue(queue_name, durable=True)

        message_ids: list[str] = []
        published = 0

        for i in range(count):
            if duplicates and message_ids and random.random() < 0.3:
                # ~30% chance of re-using an existing message_id
                message_id = random.choice(message_ids)
                print(f"  [{i+1:3d}] DUPLICATE  {message_id}")
            else:
                message_id = str(uuid.uuid4())
                message_ids.append(message_id)
                print(f"  [{i+1:3d}] NEW        {message_id}")

            payload = {
                "index": i,
                "user_id": random.randint(1000, 9999),
                "action": random.choice(["click", "purchase", "view", "signup"]),
                "value": round(random.uniform(1.0, 500.0), 2),
            }

            msg_body = json.dumps(
                {
                    "message_id": message_id,
                    "event_type": event_type,
                    "payload": payload,
                }
            ).encode()

            await exchange.publish(
                aio_pika.Message(
                    body=msg_body,
                    delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                    message_id=message_id,
                ),
                routing_key=queue_name,
            )
            published += 1

        print(f"\nPublished {published} messages ({count - len(set(message_ids))} duplicates).")


def main() -> None:
    parser = argparse.ArgumentParser(description="Publish test events to RabbitMQ")
    parser.add_argument("--url", default="amqp://guest:guest@localhost:5672/", help="RabbitMQ URL")
    parser.add_argument("--exchange", default="events", help="Exchange name")
    parser.add_argument("--queue", default="events.process", help="Queue / routing key")
    parser.add_argument("--count", type=int, default=10, help="Number of messages")
    parser.add_argument("--duplicates", action="store_true", help="Include duplicate message IDs")
    parser.add_argument("--event-type", default="user.action", help="Event type")
    args = parser.parse_args()

    asyncio.run(
        publish(
            rabbitmq_url=args.url,
            exchange_name=args.exchange,
            queue_name=args.queue,
            count=args.count,
            duplicates=args.duplicates,
            event_type=args.event_type,
        )
    )


if __name__ == "__main__":
    main()
