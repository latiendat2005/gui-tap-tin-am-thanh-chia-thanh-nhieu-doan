from Crypto.PublicKey import RSA

# Tạo khóa 2048-bit
key = RSA.generate(2048)

# Xuất khóa riêng
private_key = key.export_key()
with open("keys/private.pem", "wb") as f:
    f.write(private_key)

# Xuất khóa công khai
public_key = key.publickey().export_key()
with open("keys/public.pem", "wb") as f:
    f.write(public_key)

print("Khóa RSA 2048-bit đã được tạo và lưu vào thư mục 'keys/'")
