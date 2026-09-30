# Architecture decisions

## Database

We chose PostgreSQL for the billing database over MySQL. The deciding factors were stronger ACID
guarantees and the team's existing familiarity with PostgreSQL's tooling and extension ecosystem.

## Message queue

We chose Kafka for the event pipeline. The deciding factors were high-throughput ordered delivery
and our existing ops expertise. Redis Streams was considered but ruled out due to limited retention.

## Authentication

We use JWT with short-lived access tokens (15 min) and refresh tokens stored in an httpOnly cookie.
Password hashing uses bcrypt with cost factor 12.
