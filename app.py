from flask import Flask, request, render_template, jsonify
import pandas as pd
import mysql.connector
from io import BytesIO
import os
from apyori import apriori
from mlxtend.frequent_patterns import apriori as mlxtend_apriori, association_rules


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
        df['Tanggal'] = pd.to_datetime(df['Tanggal']).dt.date

        # Memasukkan data ke tabel transaksi (data unik berdasarkan transaksi_id)
        transaksi_df = df[['No Transaksi', 'Tanggal']].drop_duplicates()
        for index, row in transaksi_df.iterrows():
            transaksi_id = row['No Transaksi']

            # Pengecekan apakah transaksi_id sudah ada
            cursor.execute("SELECT * FROM transaksi WHERE transaksi_id = %s", (transaksi_id,))
            existing_transaksi = cursor.fetchone()

            if not existing_transaksi:  # Jika transaksi_id belum ada, baru insert
                sql = "INSERT INTO transaksi (transaksi_id, tanggal) VALUES (%s, %s)"
                val = (row['No Transaksi'], row['Tanggal'])
                cursor.execute(sql, val)

        # Memasukkan data ke tabel detailTransaksi (tanpa pengecekan detail_transaksi_id)
        for index, row in df.iterrows():
            sql = "INSERT INTO detailTransaksi (transaksi_id, nama_barang) VALUES (%s, %s)"
            val = (row['No Transaksi'], row['Nama Barang'])
            cursor.execute(sql, val)

        mydb.commit()
        mydb.close()

        return "Migrasi data selesai!"
    except Exception as e:
        return f"Terjadi kesalahan saat mengimpor data: {e}"


def apply_apriori(start_date, end_date):
    try:
        # Koneksi ke database
        mydb = mysql.connector.connect(
            host=app.config['MYSQL_HOST'],
            user=app.config['MYSQL_USER'],
            password=app.config['MYSQL_PASSWORD'],
            database=app.config['MYSQL_DB']
        )
        
        cursor = mydb.cursor()
        # Ambil data transaksi berdasarkan rentang tanggal
        query = """
        SELECT t.transaksi_id, t.tanggal, dt.nama_barang 
        FROM detailTransaksi dt
        JOIN transaksi t ON dt.transaksi_id = t.transaksi_id
        WHERE t.tanggal BETWEEN %s AND %s
        """
        cursor.execute(query, (start_date, end_date))
        result = cursor.fetchall()  # Ambil hasil query

        # Konversi hasil query ke DataFrame
        df = pd.DataFrame(result, columns=['No Transaksi', 'Tanggal', 'Nama Barang'])

        # Filter transaksi yang memiliki lebih dari 1 item
        df_trans_count = df.groupby("No Transaksi").size()
        df = df[df["No Transaksi"].isin(df_trans_count[df_trans_count > 1].index)]

        # Mengubah DataFrame mybasket menjadi boolean
        mybasket = (df.groupby(["No Transaksi", "Nama Barang"])
                    .size().unstack().reset_index().fillna(0)
                    .set_index("No Transaksi"))

        # Konversi ke boolean: nilai > 0 menjadi True, nilai lainnya menjadi False
        mybasket_sets = mybasket > 0

        # Mencari frequent itemsets
        input_support = 0.005
        frequent_itemsets = mlxtend_apriori(mybasket_sets, min_support=input_support, use_colnames=True).sort_values(by='support', ascending=False)

        # Membuat aturan asosiasi berdasarkan frequent itemsets
        input_confidence = 0.1
        rules = association_rules(frequent_itemsets, metric="confidence", min_threshold=input_confidence)
        rules = rules[["antecedents", "consequents", "antecedent support", "consequent support", "support", "confidence", "lift"]]
        rules.sort_values("confidence", ascending=False, inplace=True)

        # Format hasil Apriori untuk ditampilkan
        formatted_results = []
        for _, row in rules.iterrows():
            antecedents = ', '.join(list(row['antecedents']))
            consequents = ', '.join(list(row['consequents']))
            support = round(row['support'], 3)
            confidence = round(row['confidence'], 3)
            lift = round(row['lift'], 3)
            formatted_results.append(f"{antecedents} -> {consequents} (Support: {support}, Confidence: {confidence}, Lift: {lift})")

        # Simpan hasil analisis ke tabel asosiasi
        if not rules.empty:
            sql = "INSERT INTO asosiasi (min_support, min_confidence, start_date, end_date) VALUES (%s, %s, %s, %s)"
            val = (input_support, input_confidence, start_date, end_date)
            cursor.execute(sql, val)
            last_row_id = cursor.lastrowid

            # Simpan detail aturan asosiasi ke tabel detail_asosiasi
            for _, row in rules.iterrows():
                antecedents = ', '.join(list(row['antecedents']))
                consequents = ', '.join(list(row['consequents']))
                support = round(row['support'], 3)
                confidence = round(row['confidence'], 3)
                lift = round(row['lift'], 3)
                sql = """
                INSERT INTO detail_asosiasi 
                (asosiasi_id, antecedent, consequent, support, confidence, lift) 
                VALUES (%s, %s, %s, %s, %s, %s)
                """
                val = (last_row_id, antecedents, consequents, support, confidence, lift)
                cursor.execute(sql, val)

        mydb.commit()
 # Ambil kembali data detail asosiasi dari database untuk menyesuaikan tampilannya
        query = """
        SELECT detail_asosiasi.antecedent, detail_asosiasi.consequent, 
               detail_asosiasi.support, detail_asosiasi.confidence, detail_asosiasi.lift
        FROM detail_asosiasi
        WHERE detail_asosiasi.asosiasi_id = %s
        """
        cursor.execute(query, (last_row_id,))
        detail_results = cursor.fetchall()

        # Format hasil untuk ditampilkan
        formatted_results = []
        for idx, row in enumerate(detail_results, start=1):
            antecedents = row[0]
            consequents = row[1]
            formatted_results.append({
                'No': idx,
                'Nama Paket': f"Paket {antecedents} dan {consequents}"
            })

        mydb.close()
        return formatted_results

    except mysql.connector.Error as err:
        return f"Terjadi kesalahan koneksi database: {err}"
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
