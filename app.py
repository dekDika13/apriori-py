from flask import Flask, request, render_template, jsonify
import pandas as pd
import mysql.connector
from io import BytesIO
import os
from apyori import apriori

app = Flask(__name__)

# Konfigurasi database (sesuaikan dengan pengaturan Anda)
app.config['MYSQL_HOST'] = 'localhost'
app.config['MYSQL_USER'] = 'root'
app.config['MYSQL_PASSWORD'] = ''
app.config['MYSQL_DB'] = 'retail'

# Konfigurasi untuk pengunggahan file
app.config['UPLOAD_FOLDER'] = 'uploads'  # Folder untuk menyimpan file yang diunggah

def create_tables():
    mydb = mysql.connector.connect(
        host=app.config['MYSQL_HOST'],
        user=app.config['MYSQL_USER'],
        password=app.config['MYSQL_PASSWORD'],
        database=app.config['MYSQL_DB']
    )

    cursor = mydb.cursor()

    # Membuat tabel transaksi (query sama seperti sebelumnya)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS transaksi (
        transaksi_id VARCHAR(255) PRIMARY KEY,
        tanggal DATE
    )
    """)

    # Membuat tabel detailTransaksi (query sama seperti sebelumnya)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS detailTransaksi (
        detail_transaksi_id INT AUTO_INCREMENT PRIMARY KEY,
        transaksi_id VARCHAR(255),
        nama_barang VARCHAR(255),
        FOREIGN KEY (transaksi_id) REFERENCES transaksi(transaksi_id)
    )
    """)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS asosiasi (
        asosiasi_id INT AUTO_INCREMENT PRIMARY KEY,
        name VARCHAR(50) NOT NULL,
        min_support INT,
        min_confidence INT,
        start_date DATETIME,  -- Menggunakan DATETIME untuk menyimpan tanggal dan waktu
        end_date DATETIME    -- Menggunakan DATETIME untuk menyimpan tanggal dan waktu
    )
    """)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS detail_asosiasi (
        detail_asosiasi_id INT AUTO_INCREMENT PRIMARY KEY,
        asosiasi_id INT,
        antecedent INT,
        consequent INT,
        support INT,
        confidence INT,
        lift INT,
        FOREIGN KEY (asosiasi_id) REFERENCES asosiasi(asosiasi_id)
    )
    """)
    mydb.commit()
    mydb.close()

    return "Tabel berhasil dibuat!"  # Atau kembalikan status lain yang sesuai
# Fungsi untuk import data
def import_data(file_stream):
    try:
        mydb = mysql.connector.connect(
            host=app.config['MYSQL_HOST'],
            user=app.config['MYSQL_USER'],
            password=app.config['MYSQL_PASSWORD'],
            database=app.config['MYSQL_DB']
        )

        cursor = mydb.cursor()

        # Membaca data dari file Excel
        df = pd.read_excel(file_stream) # Ganti dengan path file Excel Anda

        # Konversi kolom tanggal ke format DATE
        df['tanggal'] = pd.to_datetime(df['tanggal']).dt.date

        # Memasukkan data ke tabel transaksi (data unik berdasarkan transaksi_id)
        transaksi_df = df[['transaksi_id', 'tanggal']].drop_duplicates()
        for index, row in transaksi_df.iterrows():
            transaksi_id = row['transaksi_id']

            # Pengecekan apakah transaksi_id sudah ada
            cursor.execute("SELECT * FROM transaksi WHERE transaksi_id = %s", (transaksi_id,))
            existing_transaksi = cursor.fetchone()

            if not existing_transaksi:  # Jika transaksi_id belum ada, baru insert
                sql = "INSERT INTO transaksi (transaksi_id, tanggal) VALUES (%s, %s)"
                val = (row['transaksi_id'], row['tanggal'])
                cursor.execute(sql, val)

        # Memasukkan data ke tabel detailTransaksi (tanpa pengecekan detail_transaksi_id)
        for index, row in df.iterrows():
            sql = "INSERT INTO detailTransaksi (transaksi_id, nama_barang) VALUES (%s, %s)"
            val = (row['transaksi_id'], row['nama_barang'])
            cursor.execute(sql, val)

        mydb.commit()
        mydb.close()

        return "Migrasi data selesai!"
    except Exception as e:
        return f"Terjadi kesalahan saat mengimpor data: {e}"


def apply_apriori(start_date, end_date):
    try:
        mydb = mysql.connector.connect(
            host=app.config['MYSQL_HOST'],
            user=app.config['MYSQL_USER'],
            password=app.config['MYSQL_PASSWORD'],
            database=app.config['MYSQL_DB']
        )

        cursor = mydb.cursor()

        # Ambil data transaksi berdasarkan rentang tanggal
        query = """
        SELECT t.transaksi_id, dt.nama_barang 
        FROM detailTransaksi dt
        JOIN transaksi t ON dt.transaksi_id = t.transaksi_id
        WHERE t.tanggal BETWEEN %s AND %s
        """
        cursor.execute(query, (start_date, end_date))
        result = cursor.fetchall()  # Ambil hasil query

        # Buat dictionary untuk mengelompokkan barang per transaksi
        transactions = {}
        for transaksi_id, nama_barang in result:
            if transaksi_id not in transactions:
                transactions[transaksi_id] = []
            transactions[transaksi_id].append(nama_barang)
        
        # Konversi ke format list of lists yang diperlukan untuk apriori
        transaction_list = list(transactions.values())

        # Terapkan algoritma Apriori
        results = list(apriori(transaction_list, min_support=0.00001, min_confidence=0.01, min_lift=1, min_length=2))

        # Format hasil Apriori untuk ditampilkan
        formatted_results = []
        for item in results:
            pair = ", ".join(item.items)
            support = str(round(item.support, 3))
            confidence = str(round(item.ordered_statistics[0].confidence, 3))
            lift = str(round(item.ordered_statistics[0].lift, 3))
            formatted_results.append(f"{pair} (Support: {support}, Confidence: {confidence}, Lift: {lift})")

        # Simpan hasil analisis ke tabel asosiasi (konversi results menjadi string)
        if results:
            # Konversi results menjadi string
            results_str = str(results)
            sql = "INSERT INTO asosiasi (min_support, min_confidence, start_date, end_date) VALUES (%s, %s, %s, %s)"
            val = (0.00001, 0.01, start_date, end_date) 
            cursor.execute(sql, val)
            last_row_id = cursor.lastrowid
        else:
            last_row_id = None  # Atur last_row_id menjadi None jika tidak ada hasil

        # Ambil ID dari asosiasi yang baru dimasukkan
        for item in results:
            for rule in item.ordered_statistics:
                antecedent = ", ".join(rule.items_base)
                consequent = ", ".join(rule.items_add)
                # Simpan detail aturan asosiasi ke tabel detail_asosiasi
                sql = """
                INSERT INTO detail_asosiasi 
                (asosiasi_id, antecedent, consequent, support, confidence, lift) 
                VALUES (%s, %s, %s, %s, %s, %s)
                """
                val = (
                    last_row_id,
                    antecedent,
                    consequent,
                    int(item.support * 100),  # Convert support to integer (e.g., 0.2 -> 20)
                    int(rule.confidence * 100),  # Convert confidence to integer
                    int(rule.lift * 100)  # Convert lift to integer
                )
                cursor.execute(sql, val)

        mydb.commit()
        mydb.close()

        return formatted_results

    except Exception as e:
        return f"Terjadi kesalahan saat melakukan analisis Apriori: {e}"

# Route untuk melakukan analisis Apriori dan menyimpan hasilnya ke database
@app.route('/apriori', methods=['GET', 'POST'])
def apriori_route():
    if request.method == 'POST':
        start_date = request.form['start_date']
        end_date = request.form['end_date']
        results = apply_apriori(start_date, end_date)
        return render_template('apriori.html', results=results)
    else:
        return render_template('apriori.html')

@app.route('/create_tables')
def create_tables_route():
    return create_tables()

@app.route('/', methods=['GET', 'POST'])
def upload_file():
    if request.method == 'POST':
        if 'file' not in request.files:
            return "Tidak ada file yang diunggah"

        file = request.files['file']
        if file.filename == '':
            return "Tidak ada file yang dipilih"

        if file:
            file_stream = BytesIO(file.read())
            return import_data(file_stream)
    else:
        return render_template('upload.html')

if __name__ == '__main__':
    app.run(debug=True)
