# Quick note:

  On your design question: I'd keep the explicit IBApp pattern for orders     
  rather than auto-initialising inside OrderApp. The _ensure_connected_app    
  pattern works well for one-shot read-only requests (connect → fetch →     
  disconnect), but orders are different — you want the connection to be
  long-lived, deliberate, and shared across multiple calls. Auto-creating a
  connection inside place_order would hide whether you're actually talking to
  the right TWS session, and TWS also tracks open orders by connection/client
  ID, so a fresh connection mid-session can cause issues.