-- Revert procost to catalog defaults.
ALTER FUNCTION textlike(text, text) COST 1;
ALTER FUNCTION textregexeq(text, text) COST 1;
