---
name: False positive / false negative
about: A finding that does not apply, or running software that MCA does not find
labels: detection
---

**Type**
- [ ] False positive: MCA reports a finding that does not apply
- [ ] False negative: MCA does not find running software

**Checkmk and MCA version**
e.g. Checkmk 2.5.0p15, MCA 1.0.0

**Host**
Operating system and the software in question (e.g. "Debian 12 with
PostgreSQL 16 from the distribution packages"). No host names needed.

**What MCA shows or misses**
The finding text or the subsystem you expected.

**Support file (optional)**
Create it with `mcactl support-data --host <host>` and attach only the
encrypted `.json.enc` file. Never attach unencrypted data: issues are
public.
