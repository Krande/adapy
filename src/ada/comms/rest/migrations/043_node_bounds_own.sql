-- 043_node_bounds_own.sql -- a node's OWN geometry's box, beside the box of its whole subtree.
--
-- A node that draws geometry of its own AND has children is more than its children: a box can meet
-- its own geometry and none of theirs. The "partially within" fold (`db/node_bounds.py`) answers such
-- a node when its own box meets the query box. NULL when the provider did not say -- a leaf, whose
-- own box is its box, or a node with nothing drawn of its own -- and the fold reads the node as
-- before.

ALTER TABLE node_bounds
    ADD COLUMN IF NOT EXISTS own_min_x DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS own_min_y DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS own_min_z DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS own_max_x DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS own_max_y DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS own_max_z DOUBLE PRECISION;
