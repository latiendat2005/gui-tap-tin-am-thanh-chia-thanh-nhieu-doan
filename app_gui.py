
from flask import Flask, render_template, request, redirect, url_for, session, send_file
from Crypto.Cipher import DES3, PKCS1_OAEP
from Crypto.PublicKey import RSA
from Crypto.Signature import pkcs1_15
from Crypto.Hash import SHA512
import os, base64, time, hashlib, zipfile, io
import requests
from mutagen.mp3 import MP3

app = Flask(__name__)
UPLOAD_FOLDER = 'uploads'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.secret_key = 'btl-an-toan-am-thanh-2025'  # 👈 Bắt buộc để dùng session
# Padding cho Triple DES
def pad(data):
    while len(data) % 8 != 0:
        data += b' '
    return data

# Mã hóa Triple DES
def encrypt_des3(data, key, iv):
    cipher = DES3.new(key, DES3.MODE_CBC, iv)
    return cipher.encrypt(pad(data))

# Ký metadata
def sign_metadata(metadata):
    private_key = RSA.import_key(open("keys/private.pem").read())
    h = SHA512.new(metadata)
    return pkcs1_15.new(private_key).sign(h)

# Chia file thành 3 phần
def split_file(data, parts=3):
    size = len(data)
    part_size = size // parts
    return [data[i*part_size:(i+1)*part_size] if i < parts-1 else data[i*part_size:] for i in range(parts)]

@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        file = request.files.get('audio')
        if not file or not file.filename.endswith('.mp3'):
            return "❌ Vui lòng chọn file .mp3 hợp lệ!"

        filename = os.path.join(UPLOAD_FOLDER, f"recording_{int(time.time())}.mp3")
        file.save(filename)

        with open(filename, 'rb') as f:
            audio_data = f.read()

        # Lấy thời lượng mp3
        audio_info = MP3(filename)
        duration = int(audio_info.info.length)

        # Metadata
        metadata = f"{os.path.basename(filename)}|{int(time.time())}|{duration}".encode()
        signature = sign_metadata(metadata)

        # Session key Triple DES và mã hóa bằng RSA-OAEP
        session_key = os.urandom(24)
        receiver_pubkey = RSA.import_key(open("keys/public.pem").read())
        encrypted_session_key = PKCS1_OAEP.new(receiver_pubkey).encrypt(session_key)

        segments = split_file(audio_data)

        zip_path = os.path.join(UPLOAD_FOLDER, "output.zip")
        with zipfile.ZipFile(zip_path, 'w') as zipf:
            for i, part in enumerate(segments):
                iv = os.urandom(8)
                cipher = encrypt_des3(part, session_key, iv)
                hash_val = hashlib.sha512(iv + cipher).hexdigest()

                # Ghi từng phần vào zip
                zipf.writestr(f"segment_{i+1}.bin", cipher)
                zipf.writestr(f"iv_{i+1}.bin", iv)
                zipf.writestr(f"hash_{i+1}.txt", hash_val)
                zipf.writestr(f"sig_{i+1}.sig", signature)

            zipf.writestr("metadata.txt", metadata)
            zipf.writestr("session_key_rsa.bin", encrypted_session_key)

        return render_template("index.html", zipname="output.zip")

    return render_template("index.html")



@app.route('/handshake', methods=['POST'])
def handshake():
    message = request.form.get('message')
    if message == "Hello!":
        return "Ready!", 200
    return "Invalid message", 400


@app.route('/verify', methods=['GET', 'POST'])
def verify():
    if not session.get('room_access'):
        return redirect(url_for('room'))

    result = None

    if request.method == 'POST':
        file = request.files.get('zipfile')
        callback_url = request.form.get('callback_url')

        if file:
            zip_bytes = file.read()
            try:
                with zipfile.ZipFile(io.BytesIO(zip_bytes), 'r') as z:
                    metadata = z.read("metadata.txt")
                    encrypted_session_key = z.read("session_key_rsa.bin")

                    # 🔐 Giải mã session key bằng private key
                    private_key = RSA.import_key(open("keys/private.pem").read())
                    session_key = PKCS1_OAEP.new(private_key).decrypt(encrypted_session_key)

                    if not session_key or len(session_key) != 24:
                        result = "❌ Khóa phiên không hợp lệ!"
                        raise Exception(result)

                    # ✅ Xác minh chữ ký
                    sig = z.read("sig_1.sig")  # Các phần đều dùng cùng chữ ký
                    public_key = RSA.import_key(open("keys/public.pem").read())
                    h = SHA512.new(metadata)
                    pkcs1_15.new(public_key).verify(h, sig)

                    # 🧩 Xác minh + giải mã 3 phần
                    full_data = b""
                    for i in range(1, 4):
                        iv = z.read(f"iv_{i}.bin")
                        cipher = z.read(f"segment_{i}.bin")
                        hash_check = z.read(f"hash_{i}.txt").decode()

                        # Kiểm tra hash
                        if hashlib.sha512(iv + cipher).hexdigest() != hash_check:
                            raise Exception(f"❌ Đoạn {i} bị lỗi – hash không khớp!")

                        # Giải mã Triple DES
                        from Crypto.Cipher import DES3
                        des = DES3.new(session_key, DES3.MODE_CBC, iv)
                        plaintext = des.decrypt(cipher)
                        full_data += plaintext

                    # ✨ Ghi lại thành file mp3
                    output_path = os.path.join(UPLOAD_FOLDER, "output_received.mp3")
                    with open(output_path, 'wb') as f:
                        f.write(full_data.rstrip(b' '))  # Bỏ padding

                    result = "✅ File âm thanh hợp lệ – tất cả đoạn đều xác thực!"
            except Exception as e:
                result = f"Lỗi khi xác minh/gải mã: {e}"

            # ✅ Gửi phản hồi ACK/NACK nếu có callback_url
            if callback_url:
                try:
                    if result.startswith("✅"):
                        requests.post(callback_url, data={
                            "status": "ACK",
                            "message": "Âm thanh hợp lệ. Tất cả chữ ký và hash đều đúng."
                        })
                    else:
                        requests.post(callback_url, data={
                            "status": "NACK",
                            "message": result
                        })
                except Exception as e:
                    print(f"[⚠️] Không thể gửi phản hồi về người gửi: {e}")

    return render_template("Verify.html", result=result, audio_file="output_received.mp3")




@app.route('/download/<filename>')
def download_file(filename):
    filepath = os.path.join(UPLOAD_FOLDER, filename)
    if os.path.exists(filepath):
        return send_file(filepath, as_attachment=True)
    return f"File {filename} không tồn tại", 404



@app.route('/room', methods=['GET', 'POST'])
def room():
    if request.method == 'POST':
        password = request.form['room_pass']
        if password == '123456':  # Kiểm tra mã phòng
            session['room_access'] = True
            return redirect(url_for('verify'))  # Chuyển hướng đến trang xác minh
        else:
            return render_template('room.html', error="🚫 Sai mã phòng!")
    return render_template('room.html')

@app.route('/logout')
def logout():
    session.pop('room_access', None)
    return redirect(url_for('room'))



@app.route('/delete/<filename>')
def delete_file(filename):
    filepath = os.path.join(UPLOAD_FOLDER, filename)
    if os.path.exists(filepath):
        os.remove(filepath)
        return redirect(url_for('index'))  # Quay lại trang chính sau khi xóa
    return f"File {filename} không tồn tại", 404


@app.route('/send_file', methods=['POST'])
def send_file_to_another_machine():
    file = request.files.get('file')
    target_ip = request.form.get('target_ip')
    callback_ip = request.form.get('callback_ip')

    target_url = f'http://{target_ip}:8001/receive_file'
    callback_url = f'http://{callback_ip}:8000/ack_handler'

    if file:
        try:
            original_name = file.filename

            print(f"📤 Gửi file: {original_name} ➜ {target_url}")

            response = requests.post(
                target_url,
                files={'file': (original_name, file.stream, file.mimetype)},
                data={
                    'callback_url': callback_url,
                    'original_name': original_name
                },
                timeout=10
            )

            data = response.json()
            if response.status_code == 200 and data.get('status') == 'success':
                return f'''
                    <h2>✅ Gửi thành công!</h2>
                    <p><strong>Đã lưu tại máy nhận:</strong> {data.get("file_url")}</p>
                
                '''
            else:
                return f"❌ Gửi thất bại: {data.get('message')}", response.status_code

        except Exception as e:
            return f"❌ Lỗi gửi file: {str(e)}", 500

    return "❌ Không có file nào được chọn", 400



@app.route('/ack_handler', methods=['POST'])
def ack_handler():
    print("📥 Đã vào route /ack_handler")  # 👈 in dòng này trước
    print("🔥 Đã nhận phản hồi từ máy nhận")
    status = request.form.get("status")
    message = request.form.get("message")
    print(f"📩 {status} – {message}")
    return "OK", 200


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8000, debug=True)
