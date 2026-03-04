---
title: No Authentication/Access Control for Emotion Data
status: pending
priority: p1
issue_id: 001
tags:
  - code-review
  - security
  - authentication
  - HIPAA
  - GDPR
dependencies: []
---

# Problem Statement

The application has **no user authentication mechanism**. Anyone with access to the Gradio interface can view, modify, or delete sensitive emotion and anxiety scores. This represents a critical security vulnerability for mental health data.

**Why it matters:**
- Emotion scores and anxiety indices are **mental health information** (protected health information under HIPAA, special category data under GDPR)
- Unauthorized access could expose sensitive psychological states
- No audit trail of who accessed what data
- Violates HIPAA Security Rule §164.312(a)(1) - Access Control
- Violates GDPR Article 32 - Security of Processing

**Current State:**
- `src/db.py` uses hardcoded credentials: `user=os.getenv("SEEKDB_USER", "root")`
- No authentication checks in `app.py` before displaying data
- `.env.example` shows `SEEKDB_PASSWORD=` (empty by default)
- Emotion data accessible to anyone with URL access

---

# Findings

**Evidence from code review:**

| File | Issue | Impact |
|------|-------|--------|
| `src/db.py:15-20` | Hardcoded root credentials | No access control |
| `app.py:560-720` | No auth checks in UI | All data visible |
| `.env.example:5` | Empty password by default | Wide open access |

**Security Risk Level:** 🔴 **CRITICAL**

**Compliance Gaps:**
- HIPAA Security Rule - Access Control: ❌ FAIL
- HIPAA Security Rule - Audit Controls: ❌ FAIL
- GDPR Article 32 - Security of Processing: ❌ FAIL
- GDPR Article 9 - Special Category Data: ❌ No explicit consent

---

# Proposed Solutions

## Solution A: Implement User Authentication (RECOMMENDED)

**Description:** Add user authentication before Phase 3 deployment.

**Implementation:**
```python
# New file: src/auth.py
import hashlib
import os
from functools import wraps
from datetime import datetime, timedelta
import jwt

SECRET_KEY = os.getenv("JWT_SECRET")

def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()

def verify_password(password: str, hashed: str) -> bool:
    return hash_password(password) == hashed

def create_token(user_id: int) -> str:
    payload = {
        "user_id": user_id,
        "exp": datetime.utcnow() + timedelta(hours=24)
    }
    return jwt.encode(payload, SECRET_KEY, algorithm="HS256")

def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.headers.get("Authorization")
        if not token:
            return {"error": "Unauthorized"}, 401
        try:
            jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
        except:
            return {"error": "Invalid token"}, 401
        return f(*args, **kwargs)
    return decorated

# In app.py
@app.login("/login", methods=["POST"])
def login():
    username = request.form.get("username")
    password = request.form.get("password")
    # Verify credentials and return token
```

**Pros:**
- ✅ Proper security foundation
- ✅ Enables user-specific data isolation
- ✅ Enables audit logging
- ✅ Industry-standard approach

**Cons:**
- ❌ Requires significant development effort
- ❌ Requires UI changes (login screen)
- ❌ Requires session management

**Effort:** Large (3-5 days)
**Risk:** Low (well-understood pattern)

---

## Solution B: Row-Level Security with Application Checks

**Description:** Add user_id to all tables and filter at application level.

**Implementation:**
```sql
ALTER TABLE user_diaries ADD COLUMN user_id INT DEFAULT 1;
ALTER TABLE user_profile ADD COLUMN user_id INT DEFAULT 1;
CREATE INDEX idx_user_id ON user_diaries(user_id);
```

**Pros:**
- ✅ Simpler than full authentication
- ✅ Enables multi-user future

**Cons:**
- ❌ Still no authentication (anyone can access user_id=1)
- ❌ Doesn't address root cause
- ❌ Gives false sense of security

**Effort:** Medium (1-2 days)
**Risk:** Medium (band-aid solution)

---

## Solution C: Environment-Based Access Control

**Description:** Use environment variables to restrict Gradio access to specific IPs or require API key.

**Implementation:**
```python
# In app.py
GRADIO_API_KEY = os.getenv("GRADIO_API_KEY")
ALLOWED_IPS = os.getenv("ALLOWED_IPS", "").split(",")

if GRADIO_API_KEY:
    # Add API key authentication to Gradio
    demo.queue(auth=lambda x: x == GRADIO_API_KEY)
```

**Pros:**
- ✅ Quick implementation
- ✅ No UI changes needed

**Cons:**
- ❌ Not user-specific (all users share same key)
- ❌ No audit trail
- ❌ IP-based filtering can be bypassed

**Effort:** Small (2-4 hours)
**Risk:** Medium (limited security)

---

# Recommended Action

**Go with Solution A (User Authentication)** for production deployment.

For demo/development only:
1. Document that authentication is NOT implemented
2. Add prominent warning on launch: "⚠️ DEMO MODE - No authentication - Do not use with real health data"
3. Add environment variable flag: `DEMO_MODE=true` to skip authentication
4. Commit to implementing auth before production use

**For Phase 3 implementation:**
1. Add `requires_auth` flag to document that endpoint needs protection
2. Implement authentication in parallel with feature development
3. Test with multiple user scenarios
4. Include security audit in acceptance criteria

---

# Technical Details

**Affected Components:**
- `app.py` - All endpoints need auth decorators
- `src/db.py` - Connection management
- `src/user_store.py` - Add user_id filtering
- `src/emotion.py` - Add user_id to coupling functions
- `.env.example` - Add auth-related environment variables

**Database Changes:**
```sql
-- Add users table
CREATE TABLE users (
    id INT AUTO_INCREMENT PRIMARY KEY,
    username VARCHAR(50) UNIQUE,
    password_hash VARCHAR(64),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Add user_id to existing tables
ALTER TABLE user_diaries ADD COLUMN user_id INT DEFAULT 1;
ALTER TABLE user_profile ADD COLUMN user_id INT DEFAULT 1;
ALTER TABLE patient_diaries ADD COLUMN user_id INT DEFAULT 1;

-- Add foreign keys
ALTER TABLE user_diaries ADD CONSTRAINT fk_user
    FOREIGN KEY (user_id) REFERENCES users(id);
```

**Migration Required:** Yes - add user_id to all tables

---

# Acceptance Criteria

- [ ] Users must log in to access any health data
- [ ] Session expires after 24 hours of inactivity
- [ ] Invalid tokens are rejected with 401 status
- [ ] Each user can only access their own data (row-level security)
- [ ] Failed login attempts are logged
- [ ] Demo mode warning displayed when authentication disabled
- [ ] Security test suite passes (authentication bypass attempts)

---

# Work Log

| Date | Action | Outcome |
|------|--------|---------|
| 2026-03-03 | Code review completed | Critical finding identified |
| 2026-03-03 | Todo created | Awaiting prioritization |

---

# Resources

**Compliance References:**
- [HIPAA Security Rule §164.312(a)(1)](https://www.hhs.gov/hipaa/for-professionals/security/laws/standards/appendix-a/)
- [GDPR Article 32 - Security of Processing](https://gdpr-info.eu/art-32-gdpr/)
- [GDPR Article 9 - Special Category Data](https://gdpr-info.eu/art-9-gdpr/)

**Related Issues:**
- Todo 002 - Mental health data encryption at rest
- Todo 003 - Consent mechanism for mental health data
- Todo 007 - Audit logging for emotion data access

**Implementation References:**
- [Gradio Authentication Guide](https://www.gradio.app/docs/grpc_with_guide)
- [Python JWT Best Practices](https://pyjwt.readthedocs.io/)
