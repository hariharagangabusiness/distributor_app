"""Optional: populate the database with sample demo data so you can see
the app working immediately. Safe to skip if you want to start with a
clean, empty database (just run init_db.py instead).

Usage: python seed_demo.py
"""
import random
from datetime import date, timedelta
import db
from app import compute_line_gst, STATE_NAME_BY_CODE

random.seed(42)

# Company profile used for the demo (Maharashtra-registered distributor)
COMPANY_STATE_CODE = "27"

# category -> (HSN code, GST %) — illustrative real-world-plausible values
CATEGORY_GST = {
    "Beverages": ("2202", 28),
    "Snacks": ("2106", 12),
    "Personal Care": ("3401", 18),
    "Household": ("3402", 18),
    "Grocery Staples": ("1006", 5),
    "Dairy": ("0401", 5),
}


def run():
    db.init_db()

    # Skip if already seeded
    existing = db.query("SELECT COUNT(*) c FROM Products", one=True)["c"]
    if existing:
        print("Database already has data — skipping demo seed. "
              "Delete db/distributor.db and re-run if you want a fresh demo.")
        return

    today = date.today()

    # Company / GST settings
    db.execute("""UPDATE CompanySettings SET CompanyName=?, GSTIN=?, PAN=?, Address=?, City=?, State=?,
                StateCode=?, Pincode=?, Phone=?, Email=?, BankName=?, BankAccountName=?, BankAccountNumber=?,
                BankIFSC=?, BankBranch=?, InvoicePrefix=? WHERE SettingsID=1""",
               ("Sunrise Distributors Pvt Ltd", "27AAAAA0000A1Z5", "AAAAA0000A",
                "Plot 12, MIDC Industrial Area", "Pune", STATE_NAME_BY_CODE[COMPANY_STATE_CODE], COMPANY_STATE_CODE,
                "411019", "9820011223", "accounts@sunrisedist.in", "HDFC Bank", "Sunrise Distributors Pvt Ltd",
                "50100123456789", "HDFC0001234", "Pune MIDC Branch", "SD"))

    # Suppliers
    suppliers = [
        ("Sunrise Distributors Pvt Ltd", "Ramesh Kumar", "9820011223", "sales@sunrisedist.in"),
        ("Global FMCG Traders", "Anita Shah", "9821044556", "orders@globalfmcg.in"),
        ("Metro Wholesale Supplies", "Vijay Nair", "9833099887", "metro@wholesale.in"),
    ]
    supplier_ids = []
    for name, contact, phone, email in suppliers:
        supplier_ids.append(db.execute(
            "INSERT INTO Suppliers (SupplierName, ContactPerson, Phone, Email, Active) VALUES (?,?,?,?,1)",
            (name, contact, phone, email)))

    # Customers — mostly same state as the distributor (CGST+SGST), one
    # inter-state (IGST) so the demo shows both invoice types
    customers = [
        ("Sharma General Store", "Sunil Sharma", "9812345670", 50000, 15, "27"),
        ("City Mart Retail", "Pooja Verma", "9812345671", 100000, 30, "27"),
        ("Green Valley Supermarket", "Arjun Rao", "9812345672", 75000, 15, "27"),
        ("Highway Kirana Center", "Deepak Joshi", "9812345673", 30000, 7, "29"),
    ]
    customer_ids = []
    for name, contact, phone, limit, days, state_code in customers:
        customer_ids.append(db.execute(
            """INSERT INTO Customers (CustomerName, ContactPerson, Phone, GSTIN, State, StateCode,
            CreditLimit, CreditDays, Active) VALUES (?,?,?,?,?,?,?,?,1)""",
            (name, contact, phone, f"{state_code}AABCU{random.randint(1000,9999)}A1Z{random.randint(1,9)}",
             STATE_NAME_BY_CODE[state_code], state_code, limit, days)))

    # Products
    categories = ["Beverages", "Snacks", "Personal Care", "Household", "Grocery Staples", "Dairy"]
    product_names = [
        "Cola 500ml", "Lemon Soda 500ml", "Mineral Water 1L", "Potato Chips 100g",
        "Salted Namkeen 200g", "Biscuits Family Pack", "Toothpaste 100g", "Bath Soap 100g",
        "Shampoo Sachet Box", "Detergent Powder 1kg", "Dish Wash Bar", "Floor Cleaner 1L",
        "Basmati Rice 5kg", "Wheat Flour 5kg", "Cooking Oil 1L", "Sugar 1kg",
        "Tea Powder 250g", "Instant Noodles Box", "Toor Dal 1kg", "Salt 1kg",
        "UHT Milk 1L", "Butter 500g", "Cheese Slices", "Ghee 1L",
        "Chocolate Bar Box", "Candy Jar 500g", "Coffee Powder 100g", "Ketchup 500g",
    ]
    product_ids = []
    for i, name in enumerate(product_names):
        cost = round(random.uniform(30, 400), 2)
        sell = round(cost * random.uniform(1.15, 1.4), 2)
        min_stock = random.choice([20, 30, 50])
        max_stock = min_stock * random.choice([4, 5, 6])
        category = categories[i % len(categories)]
        hsn, gst_rate = CATEGORY_GST[category]
        pid = db.execute("""INSERT INTO Products (SKU, ProductName, Category, Unit, CostPrice, SellingPrice,
                    MinStock, MaxStock, ReorderQty, DefaultSupplierID, HSNCode, GSTRate, Active)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,1)""",
                    (f"SKU{1000+i}", name, category, "CTN",
                     cost, sell, min_stock, max_stock, min_stock * 2,
                     supplier_ids[i % len(supplier_ids)], hsn, gst_rate))
        product_ids.append(pid)
        opening = random.randint(0, max_stock + 10)
        db.execute("""INSERT INTO InventoryTransactions (ProductID, TransactionDate, TransactionType,
                    QtyChange, RefType, Notes) VALUES (?,?,?,?,?,?)""",
                   (pid, (today - timedelta(days=60)).isoformat(), "Opening Stock", opening, "Manual", "Opening balance"))

    # A few purchases (last 30 days)
    for n in range(6):
        sup = random.choice(supplier_ids)
        pdate = (today - timedelta(days=random.randint(1, 28))).isoformat()
        lines = random.sample(product_ids, k=4)
        total = 0
        purchase_id = db.execute("""INSERT INTO Purchases (PONumber, SupplierID, PurchaseDate, InvoiceNumber,
                    Status, PaymentStatus, TotalAmount, AmountPaid, Notes) VALUES (?,?,?,?,?,?,?,?,?)""",
                    (f"PO-{2000+n}", sup, pdate, f"INV-SUP-{500+n}", "Received",
                     random.choice(["Paid", "Unpaid", "Partial"]), 0, 0, ""))
        for pid in lines:
            prod = db.query("SELECT * FROM Products WHERE ProductID=?", (pid,), one=True)
            qty = random.randint(10, 40)
            cost = prod["CostPrice"]
            total += qty * cost
            db.execute("INSERT INTO PurchaseLines (PurchaseID, ProductID, Qty, UnitCost, LineTotal) VALUES (?,?,?,?,?)",
                       (purchase_id, pid, qty, cost, qty * cost))
            db.execute("""INSERT INTO InventoryTransactions (ProductID, TransactionDate, TransactionType,
                        QtyChange, RefType, RefID, Notes) VALUES (?,?,?,?,?,?,?)""",
                       (pid, pdate, "Purchase", qty, "Purchase", purchase_id, f"PO-{2000+n}"))
        db.execute("UPDATE Purchases SET TotalAmount=?, AmountPaid=? WHERE PurchaseID=?",
                   (total, total if random.random() > 0.5 else round(total * 0.4, 2), purchase_id))

    # A few sales (last 30 days, including today) with full GST breakup
    for n in range(15):
        cust_idx = random.randrange(len(customer_ids))
        cust = customer_ids[cust_idx]
        cust_state_code = customers[cust_idx][5]
        is_interstate = 1 if cust_state_code != COMPANY_STATE_CODE else 0
        sdate = (today - timedelta(days=random.randint(0, 28))).isoformat()
        lines = random.sample(product_ids, k=random.randint(2, 5))
        invoice = f"SD/2025-26/{3000+n:04d}"
        sale_id = db.execute("""INSERT INTO Sales (InvoiceNumber, CustomerID, SaleDate, Status, PaymentStatus,
                    PaymentDueDate, TotalAmount, AmountReceived, Notes, PlaceOfSupplyState, PlaceOfSupplyStateCode,
                    IsInterState) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (invoice, cust, sdate, "Completed", "Unpaid", (today + timedelta(days=15)).isoformat(), 0, 0, "",
                     STATE_NAME_BY_CODE[cust_state_code], cust_state_code, is_interstate))
        taxable_total = cgst_total = sgst_total = igst_total = 0.0
        for pid in lines:
            prod = db.query("SELECT * FROM Products WHERE ProductID=?", (pid,), one=True)
            qty = random.randint(2, 15)
            price = prod["SellingPrice"]
            taxable_value = round(qty * price, 2)
            gst = compute_line_gst(taxable_value, prod["GSTRate"], is_interstate)
            taxable_total += taxable_value
            cgst_total += gst["cgst_amt"]; sgst_total += gst["sgst_amt"]; igst_total += gst["igst_amt"]
            db.execute("""INSERT INTO SalesLines (SaleID, ProductID, Qty, UnitPrice, LineTotal, HSNCode, GSTRate,
                        TaxableValue, CGSTRate, CGSTAmount, SGSTRate, SGSTAmount, IGSTRate, IGSTAmount)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                       (sale_id, pid, qty, price, taxable_value, prod["HSNCode"], prod["GSTRate"], taxable_value,
                        gst["cgst_rate"], gst["cgst_amt"], gst["sgst_rate"], gst["sgst_amt"],
                        gst["igst_rate"], gst["igst_amt"]))
            db.execute("""INSERT INTO InventoryTransactions (ProductID, TransactionDate, TransactionType,
                        QtyChange, RefType, RefID, Notes) VALUES (?,?,?,?,?,?,?)""",
                       (pid, sdate, "Sale", -qty, "Sale", sale_id, invoice))
        raw_total = taxable_total + cgst_total + sgst_total + igst_total
        total = round(raw_total)
        round_off = round(total - raw_total, 2)
        received = total if random.random() > 0.4 else round(total * random.choice([0, 0.5]), 2)
        status = "Paid" if received >= total else ("Partial" if received > 0 else "Unpaid")
        db.execute("""UPDATE Sales SET TotalAmount=?, AmountReceived=?, PaymentStatus=?, TaxableAmount=?,
                    CGSTAmount=?, SGSTAmount=?, IGSTAmount=?, RoundOff=? WHERE SaleID=?""",
                   (total, received, status, round(taxable_total, 2), round(cgst_total, 2),
                    round(sgst_total, 2), round(igst_total, 2), round_off, sale_id))

    # Expenses
    exp_categories = ["Rent", "Utilities", "Office Supplies", "Fuel", "Toll", "Loading/Unloading", "Other"]
    for n in range(10):
        db.execute("""INSERT INTO Expenses (ExpenseDate, Category, Amount, PaymentMode, PaidTo, Description)
                    VALUES (?,?,?,?,?,?)""",
                   ((today - timedelta(days=random.randint(0, 30))).isoformat(),
                    random.choice(exp_categories), round(random.uniform(500, 8000), 2),
                    random.choice(["Cash", "Bank", "UPI"]), "Vendor/Staff", "Demo expense entry"))

    # Vehicles
    vehicles = [
        ("MH12AB1234", "Truck (7T)", "Tata", "407"),
        ("MH12CD5678", "Tempo", "Mahindra", "Bolero Pickup"),
        ("MH14EF9012", "Van", "Ashok Leyland", "Dost"),
    ]
    vehicle_ids = []
    for reg, vtype, make, model in vehicles:
        vehicle_ids.append(db.execute("""INSERT INTO Vehicles (RegistrationNumber, VehicleType, Make, Model,
                    PurchaseDate, InsuranceExpiry, PermitExpiry, PUCExpiry, FitnessExpiry, CurrentOdometer, Status)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (reg, vtype, make, model, (today - timedelta(days=500)).isoformat(),
                     (today + timedelta(days=random.choice([10, 45, 90]))).isoformat(),
                     (today + timedelta(days=random.choice([20, 60, 200]))).isoformat(),
                     (today + timedelta(days=random.choice([5, 40, 100]))).isoformat(),
                     (today + timedelta(days=random.choice([15, 70, 150]))).isoformat(),
                     random.randint(20000, 80000), "Active")))

    for vid in vehicle_ids:
        db.execute("""INSERT INTO VehicleMaintenance (VehicleID, ServiceType, ServiceDate, Odometer,
                    NextDueDate, NextDueOdometer, Cost, ServiceCenter, Status, Notes)
                    VALUES (?,?,?,?,?,?,?,?,?,?)""",
                   (vid, "General Service", (today - timedelta(days=60)).isoformat(), 30000,
                    (today + timedelta(days=random.choice([3, 10, 25]))).isoformat(), 35000,
                    round(random.uniform(1500, 4000), 2), "Local Garage", "Completed", "Routine service"))

    # Employees
    employees = [
        ("Rajesh Patil", "Warehouse Supervisor", 25000),
        ("Suresh Yadav", "Delivery Driver", 18000),
        ("Meena Kulkarni", "Accounts Clerk", 20000),
        ("Sanjay More", "Sales Executive", 22000),
        ("Kavita Desai", "Office Assistant", 15000),
    ]
    employee_ids = []
    for name, role, salary in employees:
        employee_ids.append(db.execute("""INSERT INTO Employees (EmployeeName, Designation, Phone, JoinDate,
                    MonthlySalary, BankAccount, Status) VALUES (?,?,?,?,?,?,'Active')""",
                    (name, role, "98" + str(random.randint(10000000, 99999999)),
                     (today - timedelta(days=random.randint(200, 900))).isoformat(), salary,
                     "XXXX" + str(random.randint(1000, 9999)))))

    # One active advance
    emp = employee_ids[1]
    db.execute("""INSERT INTO AdvancePayments (EmployeeID, AdvanceDate, Amount, Reason, RepaymentMonths,
                MonthlyDeduction, BalanceRemaining, Status) VALUES (?,?,?,?,?,?,?,'Active')""",
               (emp, (today - timedelta(days=20)).isoformat(), 6000, "Medical emergency", 3, 2000, 6000))

    # Demo login, so the seeded data can be explored immediately without a
    # separate setup step. NOT meant for production use — see the printed
    # note below (and README section 1) for creating a real account.
    if not db.query("SELECT 1 FROM Users", one=True):
        from werkzeug.security import generate_password_hash
        db.execute("INSERT INTO Users (Username, PasswordHash, FullName, Role, Active) VALUES (?,?,?,'Admin',1)",
                   ("demo", generate_password_hash("demo1234"), "Demo Admin"))
        print("\nDemo login created — username: demo   password: demo1234")
        print("This is for exploring the demo only. Before using this for real data,")
        print("run 'python migrate_auth.py' to set your own account (or add more from")
        print("Manage Users once logged in) and change/remove the demo login.")

    print("\nDemo data seeded successfully.")
    print(f"Database: {db.DB_PATH}")


if __name__ == "__main__":
    run()
