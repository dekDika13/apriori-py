from flask import Flask, request, render_template, jsonify,make_response
import pandas as pd
import mysql.connector
from io import BytesIO
import os
from apyori import apriori
from mlxtend.frequent_patterns import apriori as mlxtend_apriori, association_rules
from fpdf import FPDF

app = Flask(__name__)

# Konfigurasi database (sesuaikan dengan pengaturan Anda)
app.config['MYSQL_HOST'] = 'localhost'
app.config['MYSQL_USER'] = 'root'
app.config['MYSQL_PASSWORD'] = ''
app.config['MYSQL_DB'] = 'retail'

# Konfigurasi untuk pengunggahan file
app.config['UPLOAD_FOLDER'] = 'uploads'  # Folder untuk menyimpan file yang diunggah


def format_date(date):
    return date.strftime('%Y-%m-%d') if date else ''
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
        name VARCHAR(255) NOT NULL,
        min_support FLOAT,
        min_confidence FLOAT,
        start_date DATETIME,  -- Menggunakan DATETIME untuk menyimpan tanggal dan waktu
        end_date DATETIME    -- Menggunakan DATETIME untuk menyimpan tanggal dan waktu
    )
    """)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS detail_asosiasi (
        detail_asosiasi_id INT AUTO_INCREMENT PRIMARY KEY,
        asosiasi_id INT,
        antecedent VARCHAR(255),
        consequent VARCHAR(255),
        support FLOAT,
        confidence FLOAT,
        lift FLOAT,
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

def apply_apriori(start_date, end_date,min_support,min_confidence,name ):
    mydb = None
    cursor = None
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
        input_support = min_support
        frequent_itemsets = mlxtend_apriori(mybasket_sets, min_support=input_support, use_colnames=True).sort_values(by='support', ascending=False)

        # Membuat aturan asosiasi berdasarkan frequent itemsets
        input_confidence = min_confidence
        rules = association_rules(frequent_itemsets, metric="confidence", min_threshold=input_confidence)
        rules = rules[["antecedents", "consequents", "antecedent support", "consequent support", "support", "confidence", "lift"]]
        rules.sort_values("confidence", ascending=False, inplace=True)

        # Simpan hasil analisis ke tabel asosiasi
        if not rules.empty:
            sql = "INSERT INTO asosiasi (min_support, min_confidence, start_date, end_date,name) VALUES (%s, %s, %s, %s,%s)"
            val = (input_support, input_confidence, start_date, end_date,name)
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
        return result_apriori(last_row_id)

    except mysql.connector.Error as err:
        return f"Terjadi kesalahan koneksi database: {err}"
    except Exception as e:
        return f"Terjadi kesalahan saat melakukan analisis Apriori: {e}"
    finally:
        if cursor:
            cursor.close()
        if mydb:
            mydb.close()

def result_apriori(id_asosiasi):
    mydb = None
    cursor = None
    try:
        mydb = mysql.connector.connect(
            host=app.config['MYSQL_HOST'],
            user=app.config['MYSQL_USER'],
            password=app.config['MYSQL_PASSWORD'],
            database=app.config['MYSQL_DB']
        )
        cursor = mydb.cursor()

        query = """
            SELECT detail_asosiasi.antecedent, detail_asosiasi.consequent, 
                detail_asosiasi.support, detail_asosiasi.confidence, detail_asosiasi.lift
            FROM detail_asosiasi
            WHERE detail_asosiasi.asosiasi_id = %s
        """
        cursor.execute(query, (id_asosiasi,))
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
        
        return formatted_results, id_asosiasi

    except mysql.connector.Error as err:
        return f"Terjadi kesalahan koneksi database: {err}"
    except Exception as e:
        return f"Terjadi kesalahan saat mengambil hasil Apriori: {e}"
    finally:
        if cursor:
            cursor.close()
        if mydb:
            mydb.close()


def get_asosiasi_details(id_asosiasi):
    cursor = None
    mydb = None
    try:
        mydb = mysql.connector.connect(
            host=app.config['MYSQL_HOST'],
            user=app.config['MYSQL_USER'],
            password=app.config['MYSQL_PASSWORD'],
            database=app.config['MYSQL_DB']
        )
        cursor = mydb.cursor()

        query = """
            SELECT antecedent, consequent, support, confidence, lift
            FROM detail_asosiasi
            WHERE asosiasi_id = %s
        """
        cursor.execute(query, (id_asosiasi,))
        details = cursor.fetchall()

        # Format data to pass to the template
        formatted_details = []
        for row in details:
            formatted_details.append({
                'antecedent': row[0],
                'consequent': row[1],
                'support': row[2],
                'confidence': row[3],
                'lift': row[4],
            })

        return formatted_details

    except mysql.connector.Error as err:
        return f"Terjadi kesalahan koneksi database: {err}", 500
    except Exception as e:
        return f"Terjadi kesalahan saat mengambil data asosiasi: {e}", 500
    finally:
        if cursor:
            cursor.close()
        if mydb:
            mydb.close()

@app.route('/view_detail_asosiasi/<int:id_asosiasi>')
def view_detail_asosiasi(id_asosiasi):
    details = get_asosiasi_details(id_asosiasi)
    if isinstance(details, str):  # If the return is an error message
        return details
    return render_template('view_asosiasi.html', details=details, id_asosiasi=id_asosiasi)


@app.route('/apriori', methods=['GET', 'POST'])
def apriori_route():
    if request.method == 'POST':
        start_date = request.form['start_date']
        end_date = request.form['end_date']
        name=request.form['name']
        min_support = float(request.form['min_support'])
        min_confidence = float(request.form['min_confidence'])
        results,id_asosiasi = apply_apriori(start_date, end_date, min_support,min_confidence,name)
        return render_template('result.html', results=results,id_asosiasi=id_asosiasi)
    else:
        return render_template('apriori.html')

@app.route('/create_tables')
def create_tables_route():
    return create_tables()

@app.route('/', methods=['GET', 'POST'])
def index():
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
        return render_template('index.html')


@app.route('/asosiasi_list', methods=['GET'])
def asosiasi_list():
    try:
        mydb = mysql.connector.connect(
            host=app.config['MYSQL_HOST'],
            user=app.config['MYSQL_USER'],
            password=app.config['MYSQL_PASSWORD'],
            database=app.config['MYSQL_DB']
        )
        cursor = mydb.cursor()

        # Query to get all associations
        query = "SELECT asosiasi_id, min_support, min_confidence, start_date, end_date, name FROM asosiasi"
        cursor.execute(query)
        asosiasi_list = cursor.fetchall()

        # Format data to pass to the template
        formatted_asosiasi_list = []
        for row in asosiasi_list:
            formatted_asosiasi_list.append({
                'asosiasi_id': row[0],
                'min_support': row[1],
                'min_confidence': row[2],
                'start_date': format_date(row[3]),
                'end_date': format_date(row[4]),
                'name': row[5],
            })

        return render_template('asosiasi_list.html', asosiasi_list=formatted_asosiasi_list)

    except mysql.connector.Error as err:
        return f"Terjadi kesalahan koneksi database: {err}"
    except Exception as e:
        return f"Terjadi kesalahan saat mengambil data asosiasi: {e}"
    finally:
        if cursor:
            cursor.close()
        if mydb:
            mydb.close()


@app.route('/asosiasi/<int:id_asosiasi>', methods=['GET'])
def view_asosiasi(id_asosiasi):
    results,id_asosiasi = result_apriori(id_asosiasi)
    return render_template('result.html', results=results,id_asosiasi=id_asosiasi)

@app.route('/download_apriori_pdf/<int:id_asosiasi>', methods=['GET'])
def download_apriori_pdf(id_asosiasi):
    # Fetch the Apriori results using the function you already created
    results_tuple = result_apriori(id_asosiasi)
    
    if not results_tuple:
        return "No results found for the provided id_asosiasi", 404

    # Extract the results from the tuple
    results = results_tuple[0]

    # Generate the PDF
    pdf = FPDF()
    pdf.add_page()

    # Set title
    pdf.set_font("Arial", 'B', 16)
    pdf.cell(200, 10, txt=f"Hasil Paket Produk", ln=True, align='C')
    
    # Add some space
    pdf.ln(10)

    pdf.set_fill_color(169, 169, 169) # Light blue
    # Set table headers
    pdf.set_font("Arial", 'B', 12)

    pdf.cell(10, 10, txt="No", border=1, align='C', fill=True)
    pdf.cell(170, 10, txt="Nama Paket", border=1, align='C', fill=True)
    pdf.ln()

    # Add table rows
    pdf.set_font("Arial", size=12)
    for row in results:
        pdf.cell(10, 10, txt=str(row['No']), border=1, align='C')
        pdf.cell(170, 10, txt=row['Nama Paket'], border=1, align='C')
        pdf.ln()

    # Output the PDF as a response
    response = make_response(pdf.output(dest='S').encode('latin1'))
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['Content-Disposition'] = f'attachment; filename=Hasil_Paket_Produk.pdf'
    
    return response


if __name__ == '__main__':
    app.run(debug=True)
