from pymongo import MongoClient


class DatabaseHandler:
    def __init__(self, db_url, db_name):
        self.client = MongoClient(db_url)
        self.db = self.client[db_name]
        self.collection = self.db['invoices']

    def check_invoice_exists(self, invoice_number):
        return self.collection.find_one({"invoice_number": invoice_number})

    def insert_invoice(self, invoice_number, data):
        self.collection.insert_one({"invoice_number": invoice_number, "data": data})
