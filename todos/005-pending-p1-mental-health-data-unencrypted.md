---
title: Mental Health Data Stored Unencrypted at Rest
status: pending
priority: p1
issue_id: 005
tags:
  - code-review
  - security
  - encryption
  - HIPAA
  - GDPR
  - mental-health
dependencies:
  - 001-pending-p1-no-authentication-emotion-data.md
---

# Problem Statement

Emotion scores and anxiety indices will be stored as plain FLOAT columns in `user_diaries` table. Mental health data is **special category data** under GDPR and requires enhanced protection. Storing it unencrypted violates compliance requirements and exposes sensitive information if the database is compromised.

**Why it matters:**
- Emotion scores reveal psychological states (depression, anxiety, stress)
- Anxiety scores indicate mental health distress levels
- Combined with physiological data, creates comprehensive health profile
- Database breach would expose highly sensitive mental health information
- HIPAA requires encryption of ePHI (electronic Protected Health Information)
- GDPR Article 9 requires appropriate security for special category data

**Current Plan Schema:**
```sql
ALTER TABLE user_diaries
ADD COLUMN emotion_score FLOAT,      -- PLAIN TEXT - UNENCRYPTED
ADD COLUMN anxiety_score FLOAT;      -- PLAIN TEXT - UNENCRYPTED
```

**Risk Assessment:**
- If database is compromised: Attacker sees all emotion/anxiety scores
- If backup is lost: Mental health data exposed
- If logs contain data: Sensitive information in log files
- No audit trail of who accessed emotion data

---

# Findings

**Compliance Gaps:**

| Regulation | Requirement | Current Status | Gap |
|------------|-------------|----------------|-----|
| HIPAA Security Rule §164.312(a)(2)(iv) | Encryption and Decryption | ❌ Not implemented | Store plain text |
| GDPR Article 32 | Security of Processing | ❌ Insufficient | No encryption for sensitive data |
| GDPR Article 9 | Special Category Data | ❌ No enhanced protection | Mental health data unencrypted |

**Data Classification:**
- **Emotion scores (0-100):** Mental health indicator
  - Low scores (<30): Possible depression, distress
  - High scores (>70): Well-being, positive mental state
  - Pattern analysis reveals mental health trends

- **Anxiety scores (0-100):** Direct anxiety indicator
  - High scores (>50): Anxiety symptoms present
  - Very high scores (>80): Severe anxiety/panic

**Attack Scenarios:**
1. **Database breach:** SQL injection, stolen credentials → All emotion scores exposed
2. **Backup exposure:** Unencrypted backups stolen → Historical mental health data exposed
3. **Insider threat:** DBA with direct access → Can view all emotion data
4. **Log injection:** Emotion scores logged → Exposed in log files

---

# Proposed Solutions

## Solution A: Application-Level Encryption (RECOMMENDED)

**Description:** Encrypt emotion_score and anxiety_score at application level before storage.

**Implementation:**
```python
# New file: src/encryption.py
import os
from cryptography.fernet import Fernet

# Load encryption key from environment
ENCRYPTION_KEY = os.getenv("EMOTION_ENCRYPTION_KEY")
if not ENCRYPTION_KEY:
    raise ValueError("EMOTION_ENCRYPTION_KEY not set - cannot store emotion data")

cipher = Fernet(ENCRYPTION_KEY)

def encrypt_emotion_score(score: float) -> str:
    """Encrypt emotion score for storage."""
    return cipher.encrypt(str(score).encode()).decode()

def decrypt_emotion_score(encrypted: str) -> float:
    """Decrypt emotion score from storage."""
    return float(cipher.decrypt(encrypted.encode()).decode())

# Generate key (one-time setup)
# python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

**Schema:**
```sql
-- Store as encrypted VARCHAR
ALTER TABLE user_diaries
ADD COLUMN emotion_score_encrypted VARCHAR(100),
ADD COLUMN anxiety_score_encrypted VARCHAR(100);
```

**Usage:**
```python
# In user_store.py
def save_diary(..., emotion_score, anxiety_score):
    emotion_encrypted = encrypt_emotion_score(emotion_score)
    anxiety_encrypted = encrypt_emotion_score(anxiety_score)

    cursor.execute("""
        INSERT INTO user_diaries (..., emotion_score_encrypted, anxiety_score_encrypted)
        VALUES (..., %s, %s)
    """, (..., emotion_encrypted, anxiety_encrypted))

def get_recent_diaries(n=30):
    cursor.execute("SELECT ..., emotion_score_encrypted, anxiety_score_encrypted FROM ...")
    for row in cursor.fetchall():
        # Decrypt when reading
        diary.emotion_score = decrypt_emotion_score(row['emotion_score_encrypted'])
        diary.anxiety_score = decrypt_emotion_score(row['anxiety_score_encrypted'])
```

**Pros:**
- ✅ Encrypts sensitive mental health data
- ✅ HIPAA compliant (encryption at rest)
- ✅ GDPR compliant (appropriate security for special category data)
- ✅ Key management via environment variable
- ✅ Transparent to application logic (encrypt/decrypt in store layer)

**Cons:**
- ❌ Requires additional library (cryptography)
- ❌ Slight performance overhead (~0.1ms per encrypt/decrypt)
- ❌ Key management required (rotation, storage)

**Effort:** Medium (2-3 hours)
**Risk:** Low (well-established pattern)

---

## Solution B: Separate Encrypted Table (Higher Security)

**Description:** Store emotion data in separate table with enhanced access controls.

**Implementation:**
```sql
-- Separate table for sensitive emotion data
CREATE TABLE user_emotion_data (
    id INT AUTO_INCREMENT PRIMARY KEY,
    diary_id INT NOT NULL UNIQUE,
    emotion_score_encrypted VARCHAR(100),
    anxiety_score_encrypted VARCHAR(100),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (diary_id) REFERENCES user_diaries(id) ON DELETE CASCADE,
    INDEX idx_diary (diary_id)
);

-- Add access control at application level
-- Only allow access to emotion data after authentication
```

**Pros:**
- ✅ Separation of concerns (clinical vs mental health data)
- ✅ Enhanced access control possible
- ✅ Easier to audit emotion data access
- ✅ Can implement additional security measures per-table

**Cons:**
- ❌ More complex schema (JOIN required)
- ❌ Additional table maintenance
- ❌ More complex queries

**Effort:** Large (4-6 hours)
**Risk:** Medium (more complex)

---

## Solution C: Database-Level Encryption (Alternative)

**Description:** Use SeekDB/MySQL built-in encryption functions.

**Implementation:**
```sql
-- Use AES_ENCRYPT / AES_DECRYPT (if available)
ALTER TABLE user_diaries
ADD COLUMN emotion_score BLOB;

-- On insert
INSERT INTO user_diaries (emotion_score)
VALUES (AES_ENCRYPT('75.5', @encryption_key));

-- On select
SELECT AES_DECRYPT(emotion_score, @encryption_key) FROM user_diaries;
```

**Pros:**
- ✅ Database handles encryption
- ✅ No application code changes for encryption

**Cons:**
- ❌ Key management in database (harder to secure)
- ❌ Less portable (database-specific)
- ❌ Performance impact (encryption on every query)
- ❌ Not all databases support this

**Effort:** Medium
**Risk:** Medium (database dependency)

---

# Recommended Action

**Go with Solution A** - Application-level encryption.

**Implementation Steps:**

1. **Setup:**
   ```bash
   # Install cryptography library
   pip install cryptography

   # Generate encryption key
   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

   # Add to .env
   EMOTION_ENCRYPTION_KEY=<generated_key>
   ```

2. **Update Schema:**
   ```sql
   -- Use VARCHAR for encrypted strings
   ALTER TABLE user_diaries
   ADD COLUMN emotion_score_encrypted VARCHAR(100),
   ADD COLUMN anxiety_score_encrypted VARCHAR(100);
   ```

3. **Update Code:**
   - Create `src/encryption.py` module
   - Update `save_diary()` to encrypt before storage
   - Update `get_recent_diaries()` to decrypt after retrieval
   - Update migration script to encrypt existing scores

4. **Key Management:**
   - Store `EMOTION_ENCRYPTION_KEY` in secure vault (not in code)
   - Rotate keys annually
   - Never log encryption keys
   - Backup keys separately (encrypted backup)

5. **Add Audit Logging:**
   ```python
   # Log all emotion data access
   def log_emotion_access(user_id, action, record_id):
       """Log when emotion data is accessed."""
       ...
   ```

**For Demo/Development:**
- Document that encryption is implemented but key is in .env
- Add option to disable encryption for development: `EMOTION_ENCRYPTION_ENABLED=false`
- Never commit encryption keys to git

---

# Technical Details

**Performance Impact:**

| Operation | Time | Impact |
|-----------|------|--------|
| Encrypt score | ~0.1ms | Negligible |
| Decrypt score | ~0.1ms | Negligible |
| Overall diary save | +0.2ms | <0.01% of total (embedding dominates) |

**Key Rotation Strategy:**
```python
# Support for key rotation
def rotate_emotion_encryption(old_key, new_key):
    """Re-encrypt all emotion scores with new key."""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT id, emotion_score_encrypted FROM user_diaries")
    for row in cursor.fetchall():
        diary_id, encrypted = row
        decrypted = decrypt_emotion_score(encrypted, old_key)
        re_encrypted = encrypt_emotion_score(decrypted, new_key)
        cursor.execute("UPDATE user_diaries SET emotion_score_encrypted = %s WHERE id = %s",
                       (re_encrypted, diary_id))

    conn.commit()
```

---

# Acceptance Criteria

- [ ] Emotion scores encrypted before database storage
- [ ] Anxiety scores encrypted before database storage
- [ ] Encryption key stored in environment variable (not in code)
- [ ] Decryption only happens after authentication
- [ ] All emotion data access logged
- [ ] Key rotation procedure documented
- [ ] Performance impact < 1ms per operation
- [ ] Encryption tests pass (encrypt/decrypt round-trip)

---

# Work Log

| Date | Action | Outcome |
|------|--------|---------|
| 2026-03-03 | Security review completed | Unencrypted storage identified |
| 2026-03-03 | Todo created | Awaiting implementation |

---

# Resources

**Dependencies:**
- Todo 001 - Authentication (must complete before this is meaningful)

**Compliance References:**
- [HIPAA Security Rule §164.312(a)(2)(iv)](https://www.hhs.gov/hipaa/for-professionals/security/laws/standards/appendix-a/) - Encryption and Decryption
- [GDPR Article 32](https://gdpr-info.eu/art-32-gdpr/) - Security of Processing
- [GDPR Article 9](https://gdpr-info.eu/art-9-gdpr/) - Special Category Data (health)

**Implementation References:**
- [Python cryptography library](https://cryptography.io/)
- [Fernet Encryption](https://cryptography.io/en/latest/fernet/) - Symmetric encryption
- [Key Management Best Practices](https://cheatsheetseries.owasp.org/cheatsheets/Key_Management_Cheat_Sheet.html)
