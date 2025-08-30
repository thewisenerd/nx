import hashlib

s = "NXFS"

i = 0
while True:
    i += 1
    buf = s + str(i)
    h = hashlib.sha1(buf.encode()).hexdigest().upper()
    if h.startswith("AF42"):
        print(f"Found: {buf} -> {h}")
        break
