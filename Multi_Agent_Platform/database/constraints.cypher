// Define constraints and indexes to enforce schema rules in Neo4j

// Uniqueness Constraints
CREATE CONSTRAINT customer_id_unique IF NOT EXISTS
FOR (c:Customer) REQUIRE c.customer_id IS UNIQUE;

CREATE CONSTRAINT product_id_unique IF NOT EXISTS
FOR (p:Product) REQUIRE p.product_id IS UNIQUE;

CREATE CONSTRAINT order_id_unique IF NOT EXISTS
FOR (o:Order) REQUIRE o.order_id IS UNIQUE;

CREATE CONSTRAINT bin_id_unique IF NOT EXISTS
FOR (b:StorageBin) REQUIRE b.bin_id IS UNIQUE;

CREATE CONSTRAINT carrier_id_unique IF NOT EXISTS
FOR (cr:Carrier) REQUIRE cr.carrier_id IS UNIQUE;

// Indexes for faster lookups
CREATE INDEX bin_zone_index IF NOT EXISTS
FOR (b:StorageBin) ON (b.zone);

CREATE INDEX carrier_region_index IF NOT EXISTS
FOR (cr:Carrier) ON (cr.region);
