// Basic schema for AI Distributed Systems Assistant

// Services and nodes
CREATE CONSTRAINT service_name IF NOT EXISTS
FOR (s:Service) REQUIRE s.name IS UNIQUE;

CREATE CONSTRAINT node_name IF NOT EXISTS
FOR (n:Node) REQUIRE n.name IS UNIQUE;

// Incidents
CREATE CONSTRAINT incident_id IF NOT EXISTS
FOR (i:Incident) REQUIRE i.id IS UNIQUE;

// Runbooks
CREATE CONSTRAINT runbook_id IF NOT EXISTS
FOR (r:Runbook) REQUIRE r.id IS UNIQUE;
