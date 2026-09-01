-- =====================================================================
-- Distributor Operations Management - Database Schema (SQLite)
-- Mirrors the MS Access schema in access_package/Build_Database.bas
-- =====================================================================

PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------------
-- Master tables
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS Suppliers (
    SupplierID      INTEGER PRIMARY KEY AUTOINCREMENT,
    SupplierName    TEXT NOT NULL,
    ContactPerson   TEXT,
    Phone           TEXT,
    Email           TEXT,
    Address         TEXT,
    GSTIN           TEXT,
    Active          INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS Customers (
    CustomerID      INTEGER PRIMARY KEY AUTOINCREMENT,
    CustomerName    TEXT NOT NULL,
    ContactPerson   TEXT,
    Phone           TEXT,
    Email           TEXT,
    Address         TEXT,
    GSTIN           TEXT,
    State           TEXT,
    StateCode       TEXT,     -- 2-digit GST state code, e.g. '27' for Maharashtra
    CreditLimit     REAL NOT NULL DEFAULT 0,
    CreditDays      INTEGER NOT NULL DEFAULT 0,
    Active          INTEGER NOT NULL DEFAULT 1,
    IsUnassignedBucket INTEGER NOT NULL DEFAULT 0  -- 1 = the single system "Unassigned" customer that
                                                    -- reconciled Stock Issue sales land under until reassigned
);

CREATE TABLE IF NOT EXISTS Products (
    ProductID       INTEGER PRIMARY KEY AUTOINCREMENT,
    SKU             TEXT UNIQUE,
    ProductName     TEXT NOT NULL,
    Category        TEXT,
    Unit            TEXT NOT NULL DEFAULT 'PCS',
    CostPrice       REAL NOT NULL DEFAULT 0,
    SellingPrice    REAL NOT NULL DEFAULT 0,
    MinStock        REAL NOT NULL DEFAULT 0,
    MaxStock        REAL NOT NULL DEFAULT 0,
    ReorderQty      REAL NOT NULL DEFAULT 0,
    DefaultSupplierID INTEGER,
    HSNCode         TEXT,               -- HSN/SAC code for GST invoices
    GSTRate         REAL NOT NULL DEFAULT 0,   -- combined GST % (e.g. 18 for 18%)
    Active          INTEGER NOT NULL DEFAULT 1,
    SchemeName      TEXT,               -- optional label for a running discount/promo scheme on this product
    SchemePercent   REAL NOT NULL DEFAULT 0,   -- % of Cost Price given as the scheme discount (0 = no scheme)
    IncentivePerUnit REAL NOT NULL DEFAULT 0,  -- flat ₹ incentive earned by the distributor per unit sold (adds to margin in P&L)
    FOREIGN KEY (DefaultSupplierID) REFERENCES Suppliers(SupplierID)
);

-- ---------------------------------------------------------------------
-- Scheme Claims (distributor-level, not tied to any one product/Stock
-- Issue) - e.g. an annual volume rebate or a company-wide promo claim
-- covering multiple products/months.
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS SchemeClaims (
    ClaimID         INTEGER PRIMARY KEY AUTOINCREMENT,
    SchemeName      TEXT NOT NULL,
    ClaimDate       TEXT NOT NULL,      -- date the claim/scheme period was raised
    ApplicableProducts TEXT,            -- free text: product(s)/line this scheme covers
    Description     TEXT,               -- scheme terms / why this claim exists
    ClaimAmount     REAL NOT NULL DEFAULT 0,   -- amount being claimed from the company
    Status          TEXT NOT NULL DEFAULT 'Pending',  -- Pending / Claimed / Received / Completed
    ClaimedAt       TEXT,
    ReceivedAt      TEXT,
    ReceivedAmount  REAL,
    Notes           TEXT,
    CreatedAt       TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ---------------------------------------------------------------------
-- Company / distributor profile (single row, used on GST invoices)
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS CompanySettings (
    SettingsID      INTEGER PRIMARY KEY CHECK (SettingsID = 1),
    CompanyName     TEXT NOT NULL DEFAULT 'Your Company Name',
    GSTIN           TEXT,
    PAN             TEXT,
    Address         TEXT,
    City            TEXT,
    State           TEXT,
    StateCode       TEXT,       -- 2-digit GST state code of the distributor's registered place of business
    Pincode         TEXT,
    Phone           TEXT,
    Email           TEXT,
    BankName        TEXT,
    BankAccountName TEXT,
    BankAccountNumber TEXT,
    BankIFSC        TEXT,
    BankBranch      TEXT,
    InvoicePrefix   TEXT NOT NULL DEFAULT 'INV',
    InvoiceTerms    TEXT DEFAULT 'Goods once sold will not be taken back. Interest @18% p.a. charged on overdue bills. Subject to local jurisdiction only.',
    NextInvoiceSeq  INTEGER NOT NULL DEFAULT 1,
    InvoiceSeqFY    TEXT,   -- financial year the NextInvoiceSeq counter applies to, e.g. '2026-27'
    -- GST Filing: scheme + reminder/email settings (see GST Filing section below)
    GstFilingScheme       TEXT NOT NULL DEFAULT 'Monthly',  -- 'Monthly' or 'QRMP'
    GstRemindersEnabled   INTEGER NOT NULL DEFAULT 0,
    GstReminderEmails     TEXT,      -- comma-separated recipient list
    GstReminderDaysBefore INTEGER NOT NULL DEFAULT 3,
    SmtpHost              TEXT,
    SmtpPort              INTEGER NOT NULL DEFAULT 587,
    SmtpUsername          TEXT,
    SmtpPassword          TEXT,
    SmtpFromEmail         TEXT,
    SmtpUseTLS            INTEGER NOT NULL DEFAULT 1
);

INSERT OR IGNORE INTO CompanySettings (SettingsID) VALUES (1);

CREATE TABLE IF NOT EXISTS Vehicles (
    VehicleID           INTEGER PRIMARY KEY AUTOINCREMENT,
    RegistrationNumber  TEXT NOT NULL UNIQUE,
    VehicleType         TEXT,
    Make                TEXT,
    Model                TEXT,
    PurchaseDate         TEXT,
    InsuranceExpiry      TEXT,
    PermitExpiry         TEXT,
    PUCExpiry            TEXT,
    FitnessExpiry        TEXT,
    CurrentOdometer      REAL NOT NULL DEFAULT 0,
    Status               TEXT NOT NULL DEFAULT 'Active'
);

CREATE TABLE IF NOT EXISTS Employees (
    EmployeeID      INTEGER PRIMARY KEY AUTOINCREMENT,
    EmployeeName    TEXT NOT NULL,
    Designation     TEXT,
    Phone           TEXT,
    JoinDate        TEXT,
    MonthlySalary   REAL NOT NULL DEFAULT 0,
    BankAccount     TEXT,
    Status          TEXT NOT NULL DEFAULT 'Active'
);

-- (Login accounts are defined further down, in the "Login" section near
-- the end of this file, alongside the other later-phase additions.)

-- ---------------------------------------------------------------------
-- Purchases (Inbound)
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS Purchases (
    PurchaseID      INTEGER PRIMARY KEY AUTOINCREMENT,
    PONumber        TEXT NOT NULL UNIQUE,
    SupplierID      INTEGER NOT NULL,
    PurchaseDate    TEXT NOT NULL,
    InvoiceNumber   TEXT,
    Status          TEXT NOT NULL DEFAULT 'Received',  -- Draft/Ordered/Received/Cancelled
    PaymentStatus   TEXT NOT NULL DEFAULT 'Unpaid',     -- Unpaid/Partial/Paid
    TotalAmount     REAL NOT NULL DEFAULT 0,
    AmountPaid      REAL NOT NULL DEFAULT 0,
    Notes           TEXT,
    -- GST fields (added for GST Filing feature - snapshot at time of entry,
    -- same pattern as Sales below). Purchases recorded before this feature
    -- shipped keep TaxableAmount/tax columns at their 0 default - they
    -- simply aren't counted in the GSTR-3B "ITC per our records" figure.
    SupplierGSTIN   TEXT,
    SupplierStateCode TEXT,               -- derived from SupplierGSTIN's first 2 digits at entry time
    IsInterState    INTEGER NOT NULL DEFAULT 0,   -- 1 = IGST applies, 0 = CGST+SGST applies
    TaxableAmount   REAL NOT NULL DEFAULT 0,
    CGSTAmount      REAL NOT NULL DEFAULT 0,
    SGSTAmount      REAL NOT NULL DEFAULT 0,
    IGSTAmount      REAL NOT NULL DEFAULT 0,
    RoundOff        REAL NOT NULL DEFAULT 0,
    ReverseCharge   INTEGER NOT NULL DEFAULT 0,
    ITCEligible     INTEGER NOT NULL DEFAULT 1,   -- 0 = blocked/ineligible credit (Sec 17(5) etc.), excluded from ITC totals
    FOREIGN KEY (SupplierID) REFERENCES Suppliers(SupplierID)
);

CREATE TABLE IF NOT EXISTS PurchaseLines (
    LineID          INTEGER PRIMARY KEY AUTOINCREMENT,
    PurchaseID      INTEGER NOT NULL,
    ProductID       INTEGER NOT NULL,
    Qty             REAL NOT NULL,
    UnitCost        REAL NOT NULL,
    LineTotal       REAL NOT NULL,
    -- GST fields (snapshot at time of purchase, mirrors SalesLines)
    HSNCode         TEXT,
    GSTRate         REAL NOT NULL DEFAULT 0,
    TaxableValue    REAL NOT NULL DEFAULT 0,
    CGSTRate        REAL NOT NULL DEFAULT 0,
    CGSTAmount      REAL NOT NULL DEFAULT 0,
    SGSTRate        REAL NOT NULL DEFAULT 0,
    SGSTAmount      REAL NOT NULL DEFAULT 0,
    IGSTRate        REAL NOT NULL DEFAULT 0,
    IGSTAmount      REAL NOT NULL DEFAULT 0,
    FOREIGN KEY (PurchaseID) REFERENCES Purchases(PurchaseID) ON DELETE CASCADE,
    FOREIGN KEY (ProductID) REFERENCES Products(ProductID)
);

-- ---------------------------------------------------------------------
-- Sales (Outbound)
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS Sales (
    SaleID          INTEGER PRIMARY KEY AUTOINCREMENT,
    InvoiceNumber   TEXT NOT NULL UNIQUE,
    CustomerID      INTEGER NOT NULL,
    SaleDate        TEXT NOT NULL,
    Status          TEXT NOT NULL DEFAULT 'Completed',  -- Draft/Completed/Cancelled
    PaymentStatus   TEXT NOT NULL DEFAULT 'Unpaid',      -- Unpaid/Partial/Paid
    PaymentDueDate  TEXT,
    TotalAmount     REAL NOT NULL DEFAULT 0,
    AmountReceived  REAL NOT NULL DEFAULT 0,
    Notes           TEXT,
    -- GST invoice fields
    PlaceOfSupplyState     TEXT,
    PlaceOfSupplyStateCode TEXT,
    IsInterState           INTEGER NOT NULL DEFAULT 0,   -- 1 = IGST applies, 0 = CGST+SGST applies
    TaxableAmount           REAL NOT NULL DEFAULT 0,
    CGSTAmount              REAL NOT NULL DEFAULT 0,
    SGSTAmount              REAL NOT NULL DEFAULT 0,
    IGSTAmount              REAL NOT NULL DEFAULT 0,
    RoundOff                REAL NOT NULL DEFAULT 0,
    ReverseCharge           INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (CustomerID) REFERENCES Customers(CustomerID)
);

CREATE TABLE IF NOT EXISTS SalesLines (
    LineID          INTEGER PRIMARY KEY AUTOINCREMENT,
    SaleID          INTEGER NOT NULL,
    ProductID       INTEGER NOT NULL,
    Qty             REAL NOT NULL,
    UnitPrice       REAL NOT NULL,
    LineTotal       REAL NOT NULL,
    -- GST invoice fields (snapshot at time of sale, product master may change later)
    HSNCode         TEXT,
    GSTRate         REAL NOT NULL DEFAULT 0,
    TaxableValue    REAL NOT NULL DEFAULT 0,
    CGSTRate        REAL NOT NULL DEFAULT 0,
    CGSTAmount      REAL NOT NULL DEFAULT 0,
    SGSTRate        REAL NOT NULL DEFAULT 0,
    SGSTAmount      REAL NOT NULL DEFAULT 0,
    IGSTRate        REAL NOT NULL DEFAULT 0,
    IGSTAmount      REAL NOT NULL DEFAULT 0,
    FOREIGN KEY (SaleID) REFERENCES Sales(SaleID) ON DELETE CASCADE,
    FOREIGN KEY (ProductID) REFERENCES Products(ProductID)
);

-- ---------------------------------------------------------------------
-- Inventory ledger (source of truth for current stock)
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS InventoryTransactions (
    TransactionID   INTEGER PRIMARY KEY AUTOINCREMENT,
    ProductID       INTEGER NOT NULL,
    TransactionDate TEXT NOT NULL,
    TransactionType TEXT NOT NULL,   -- Purchase / Sale / Adjustment-In / Adjustment-Out / Return-In / Return-Out
    QtyChange       REAL NOT NULL,   -- positive = stock in, negative = stock out
    RefType         TEXT,            -- Purchase / Sale / Manual
    RefID           INTEGER,
    Notes           TEXT,
    FOREIGN KEY (ProductID) REFERENCES Products(ProductID)
);

-- ---------------------------------------------------------------------
-- Operating expenses
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS Expenses (
    ExpenseID       INTEGER PRIMARY KEY AUTOINCREMENT,
    ExpenseDate     TEXT NOT NULL,
    Category        TEXT NOT NULL,   -- Rent/Utilities/Office/Fuel/Toll/Loading-Unloading/Misc/Other
    VehicleID       INTEGER,
    Amount          REAL NOT NULL,
    PaymentMode     TEXT,            -- Cash/Bank/UPI/Cheque
    PaidTo          TEXT,
    Description     TEXT,
    FOREIGN KEY (VehicleID) REFERENCES Vehicles(VehicleID)
);

-- ---------------------------------------------------------------------
-- Vehicle maintenance schedule
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS VehicleMaintenance (
    MaintenanceID   INTEGER PRIMARY KEY AUTOINCREMENT,
    VehicleID       INTEGER NOT NULL,
    ServiceType     TEXT NOT NULL,   -- Oil Change/Tyre/General Service/Repair/Insurance Renewal/Permit Renewal/PUC/Fitness
    ServiceDate     TEXT,
    Odometer        REAL,
    NextDueDate     TEXT,
    NextDueOdometer REAL,
    Cost            REAL NOT NULL DEFAULT 0,
    ServiceCenter   TEXT,
    Status          TEXT NOT NULL DEFAULT 'Completed',  -- Scheduled/Completed/Overdue
    Notes           TEXT,
    FOREIGN KEY (VehicleID) REFERENCES Vehicles(VehicleID)
);

-- ---------------------------------------------------------------------
-- Salary payments (monthly schedule)
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS SalaryPayments (
    PaymentID       INTEGER PRIMARY KEY AUTOINCREMENT,
    EmployeeID      INTEGER NOT NULL,
    SalaryYear      INTEGER NOT NULL,
    SalaryMonth     INTEGER NOT NULL,   -- 1-12
    GrossSalary     REAL NOT NULL DEFAULT 0,  -- MonthlySalary snapshot, before LOP deduction
    LOPDays         REAL NOT NULL DEFAULT 0,  -- loss-of-pay days applied (0 if attendance not recorded that month)
    BasicAmount     REAL NOT NULL,             -- GrossSalary minus LOP-day deduction
    Bonus           REAL NOT NULL DEFAULT 0,
    Deductions      REAL NOT NULL DEFAULT 0,
    AdvanceDeducted REAL NOT NULL DEFAULT 0,
    NetPayable      REAL NOT NULL,
    Status          TEXT NOT NULL DEFAULT 'Pending',   -- Pending/Paid
    PaymentDate     TEXT,
    PaymentMode     TEXT,
    Notes           TEXT,
    FOREIGN KEY (EmployeeID) REFERENCES Employees(EmployeeID),
    UNIQUE (EmployeeID, SalaryYear, SalaryMonth)
);

-- ---------------------------------------------------------------------
-- Advance payments to employees
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS AdvancePayments (
    AdvanceID           INTEGER PRIMARY KEY AUTOINCREMENT,
    EmployeeID          INTEGER NOT NULL,
    AdvanceDate         TEXT NOT NULL,
    Amount              REAL NOT NULL,
    Reason              TEXT,
    RepaymentMonths     INTEGER NOT NULL DEFAULT 1,
    MonthlyDeduction    REAL NOT NULL DEFAULT 0,
    BalanceRemaining    REAL NOT NULL,
    Status              TEXT NOT NULL DEFAULT 'Active',  -- Active/Closed
    FOREIGN KEY (EmployeeID) REFERENCES Employees(EmployeeID)
);

-- ---------------------------------------------------------------------
-- File attachments (generic, works for any module)
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS Attachments (
    AttachmentID    INTEGER PRIMARY KEY AUTOINCREMENT,
    ModuleName      TEXT NOT NULL,     -- 'Employee' / 'Advance' / 'Vehicle' / 'Purchase' / 'Expense'
    RecordID        INTEGER NOT NULL,
    FileName        TEXT NOT NULL,     -- original filename shown to the user
    StoredPath      TEXT NOT NULL,     -- path on disk relative to the uploads folder
    FileSize        INTEGER NOT NULL DEFAULT 0,
    ContentType     TEXT,
    UploadedAt      TEXT NOT NULL,
    Notes           TEXT
);
CREATE INDEX IF NOT EXISTS idx_attachments_lookup ON Attachments(ModuleName, RecordID);

-- ---------------------------------------------------------------------
-- Customizable (admin-defined) custom fields, for any module
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS CustomFieldDefinitions (
    FieldID         INTEGER PRIMARY KEY AUTOINCREMENT,
    ModuleName      TEXT NOT NULL,
    FieldLabel      TEXT NOT NULL,
    FieldKey        TEXT NOT NULL,      -- slug used internally, unique per module
    FieldType       TEXT NOT NULL DEFAULT 'text',  -- text / number / date / dropdown / checkbox / attachment
    DropdownOptions TEXT,               -- comma-separated options, only used when FieldType='dropdown'
    DisplayOrder    INTEGER NOT NULL DEFAULT 0,
    Active          INTEGER NOT NULL DEFAULT 1,
    UNIQUE (ModuleName, FieldKey)
);

CREATE TABLE IF NOT EXISTS CustomFieldValues (
    ValueID         INTEGER PRIMARY KEY AUTOINCREMENT,
    FieldID         INTEGER NOT NULL,
    RecordID        INTEGER NOT NULL,
    ValueText       TEXT,
    FOREIGN KEY (FieldID) REFERENCES CustomFieldDefinitions(FieldID) ON DELETE CASCADE,
    UNIQUE (FieldID, RecordID)
);
CREATE INDEX IF NOT EXISTS idx_customvalues_record ON CustomFieldValues(FieldID, RecordID);

-- ---------------------------------------------------------------------
-- Dashboard widget show/hide + order preference
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS DashboardWidgets (
    WidgetKey       TEXT PRIMARY KEY,
    Visible         INTEGER NOT NULL DEFAULT 1,
    DisplayOrder    INTEGER NOT NULL DEFAULT 0
);

-- ---------------------------------------------------------------------
-- Per-module list view column show/hide + order preference
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS ListViewColumns (
    ModuleName      TEXT NOT NULL,
    ColumnKey       TEXT NOT NULL,      -- base field key, or 'custom:<FieldID>' for a custom field
    Visible         INTEGER NOT NULL DEFAULT 1,
    DisplayOrder    INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (ModuleName, ColumnKey)
);

-- ---------------------------------------------------------------------
-- Employee Leave & Attendance
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS LeaveTypes (
    LeaveTypeID     INTEGER PRIMARY KEY AUTOINCREMENT,
    LeaveTypeName   TEXT NOT NULL,
    LeaveCode       TEXT NOT NULL,
    Active          INTEGER NOT NULL DEFAULT 1,
    DisplayOrder    INTEGER NOT NULL DEFAULT 0,
    -- Shared annual quota (days/year) for this leave type, applied to
    -- every employee alike. Set once here (Leave Types page) rather than
    -- per employee - as of the "shared quota" amendment, this column is
    -- the single source of truth for how many days of this leave type
    -- anyone gets per year.
    AnnualQuota     REAL NOT NULL DEFAULT 0
);

-- LEGACY / preserved for history only - this table is no longer read or
-- written by the app now that leave quotas are shared across all
-- employees (see LeaveTypes.AnnualQuota above). It is kept, not dropped,
-- so that anyone who had configured per-employee quotas before this
-- amendment doesn't lose that historical data; migrate_shared_leave_quota.py
-- reads from it once, to seed a sensible starting AnnualQuota per leave
-- type, and never touches it again.
CREATE TABLE IF NOT EXISTS EmployeeLeaveQuotas (
    QuotaID         INTEGER PRIMARY KEY AUTOINCREMENT,
    EmployeeID      INTEGER NOT NULL,
    LeaveTypeID     INTEGER NOT NULL,
    AnnualQuota     REAL NOT NULL DEFAULT 0,
    FOREIGN KEY (EmployeeID) REFERENCES Employees(EmployeeID) ON DELETE CASCADE,
    FOREIGN KEY (LeaveTypeID) REFERENCES LeaveTypes(LeaveTypeID) ON DELETE CASCADE,
    UNIQUE (EmployeeID, LeaveTypeID)
);

-- One row per employee per calendar month: a monthly attendance summary
-- (not a daily log) - present days, weekly-off/holiday days, and the
-- resulting Loss-of-Pay days, computed and locked in at save time from
-- the leave days entered below vs. that employee's remaining quota for
-- the year so far.
CREATE TABLE IF NOT EXISTS AttendanceMonthly (
    AttendanceID    INTEGER PRIMARY KEY AUTOINCREMENT,
    EmployeeID      INTEGER NOT NULL,
    AttYear         INTEGER NOT NULL,
    AttMonth        INTEGER NOT NULL,   -- 1-12
    DaysInMonth     REAL NOT NULL,
    PresentDays     REAL NOT NULL DEFAULT 0,
    WeeklyOffDays   REAL NOT NULL DEFAULT 0,
    LOPDays         REAL NOT NULL DEFAULT 0,   -- computed: unpaid days (absence beyond leave balance)
    Notes           TEXT,
    UpdatedAt       TEXT,
    FOREIGN KEY (EmployeeID) REFERENCES Employees(EmployeeID) ON DELETE CASCADE,
    UNIQUE (EmployeeID, AttYear, AttMonth)
);

-- Leave days taken per leave type within one AttendanceMonthly entry,
-- split into the portion covered by remaining quota (Paid) vs. the
-- portion beyond it (Unpaid, folded into the parent row's LOPDays).
CREATE TABLE IF NOT EXISTS AttendanceLeaveDetail (
    DetailID        INTEGER PRIMARY KEY AUTOINCREMENT,
    AttendanceID    INTEGER NOT NULL,
    LeaveTypeID     INTEGER NOT NULL,
    DaysTaken       REAL NOT NULL DEFAULT 0,
    PaidDays        REAL NOT NULL DEFAULT 0,
    UnpaidDays      REAL NOT NULL DEFAULT 0,
    FOREIGN KEY (AttendanceID) REFERENCES AttendanceMonthly(AttendanceID) ON DELETE CASCADE,
    FOREIGN KEY (LeaveTypeID) REFERENCES LeaveTypes(LeaveTypeID) ON DELETE CASCADE,
    UNIQUE (AttendanceID, LeaveTypeID)
);
CREATE INDEX IF NOT EXISTS idx_attendance_emp ON AttendanceMonthly(EmployeeID, AttYear, AttMonth);
CREATE INDEX IF NOT EXISTS idx_attleave_att ON AttendanceLeaveDetail(AttendanceID);

-- =====================================================================
-- GST Filing (GSTR-1 / GSTR-2B / GSTR-3B reference reports + reminders)
-- =====================================================================
-- GSTR-1 and GSTR-3B outward-supply figures are COMPUTED on the fly from
-- Sales/SalesLines (and Purchases/PurchaseLines for the ITC side) - there
-- is no stored table for them, so there's nothing to migrate/lose for
-- those. The tables below exist only for the two things that must be
-- captured or remembered: an uploaded GSTR-2B Excel file and its parsed
-- totals, and a log of reminder emails already sent (so the same due
-- date doesn't get emailed twice).

-- One row per GSTR-2B Excel file uploaded for a return period. Re-uploading
-- for the same period adds another row rather than overwriting - the most
-- recent successfully-parsed upload for a period is what the app uses for
-- reconciliation, and older uploads stay available for reference.
CREATE TABLE IF NOT EXISTS Gstr2bUploads (
    UploadID        INTEGER PRIMARY KEY AUTOINCREMENT,
    Period          TEXT NOT NULL,             -- 'YYYY-MM' - the return period this GSTR-2B statement covers
    FileName        TEXT NOT NULL,
    StoredPath      TEXT NOT NULL,             -- relative path under uploads/, same convention as Attachments
    UploadedAt      TEXT NOT NULL,
    ParseStatus     TEXT NOT NULL DEFAULT 'Pending',  -- Pending/Parsed/Failed
    ParseMessage    TEXT,                      -- human-readable detail, especially on Failed
    ITCIntegrated   REAL NOT NULL DEFAULT 0,   -- IGST available as per this GSTR-2B
    ITCCentral      REAL NOT NULL DEFAULT 0,   -- CGST available
    ITCState        REAL NOT NULL DEFAULT 0,   -- SGST/UTGST available
    ITCCess         REAL NOT NULL DEFAULT 0,
    RecordCount     INTEGER NOT NULL DEFAULT 0 -- number of B2B/CDNR/etc. line records the parser found
);
CREATE INDEX IF NOT EXISTS idx_gstr2b_period ON Gstr2bUploads(Period);

-- One row per reminder actually sent (email or in-app acknowledgement),
-- so the background reminder check never sends the same due date twice.
CREATE TABLE IF NOT EXISTS GstReminderLog (
    LogID           INTEGER PRIMARY KEY AUTOINCREMENT,
    ReturnType      TEXT NOT NULL,   -- 'GSTR-1' / 'GSTR-3B' / 'GSTR-2B' / 'PMT-06'
    Period          TEXT NOT NULL,   -- 'YYYY-MM'
    DueDate         TEXT NOT NULL,   -- ISO date the reminder was for
    SentAt          TEXT NOT NULL,
    Recipients      TEXT,
    UNIQUE (ReturnType, Period, DueDate)
);

-- =====================================================================
-- Purchase import from vendor files (e.g. an SO/Delivery Details export
-- from a supplier's own system) - remembers which of a supplier's
-- material codes maps to which of our Products, so re-uploading a file
-- from the same supplier auto-matches next time.
-- =====================================================================
CREATE TABLE IF NOT EXISTS SupplierProductMap (
    MapID           INTEGER PRIMARY KEY AUTOINCREMENT,
    SupplierID      INTEGER NOT NULL,
    VendorCode      TEXT NOT NULL,   -- the supplier's own material/item code for this product
    VendorName      TEXT,            -- the supplier's own material description, for reference
    ProductID       INTEGER NOT NULL,
    CreatedAt       TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (SupplierID) REFERENCES Suppliers(SupplierID),
    FOREIGN KEY (ProductID) REFERENCES Products(ProductID),
    UNIQUE (SupplierID, VendorCode)
);

-- =====================================================================
-- Stock Issues (van/route sales) - issue a batch of stock to a
-- salesperson (an existing Employee) in the morning, then reconcile at
-- day's end: how much of each product actually sold, how much came
-- back, and how much cash was collected. Deliberately kept separate
-- from Sales/SalesLines/GST - this is a same-day custody/cash-tally
-- tool for route/van salespeople, not itself a GST-invoiced sale.
-- =====================================================================
CREATE TABLE IF NOT EXISTS StockIssues (
    IssueID         INTEGER PRIMARY KEY AUTOINCREMENT,
    EmployeeID      INTEGER NOT NULL,           -- the salesperson stock was issued to
    IssueDate       TEXT NOT NULL,
    Status          TEXT NOT NULL DEFAULT 'Issued',  -- Issued (out with salesperson) / Reconciled (day closed out)
    CashCollected   REAL,                        -- filled in at reconciliation (cumulative, incl. later due collections) = CashAmount + BankAmount
    CashAmount      REAL NOT NULL DEFAULT 0,      -- breakdown of CashCollected: physical cash portion
    BankAmount      REAL NOT NULL DEFAULT 0,      -- breakdown of CashCollected: bank/UPI/transfer portion
    ExpectedAmount  REAL,                         -- SUM(QtySold*UnitPrice - DiscountAmount) across lines, filled in at reconciliation
    Discrepancy     REAL,                         -- CashCollected - ExpectedAmount (negative = short)
    AmountDue       REAL NOT NULL DEFAULT 0,      -- outstanding balance still owed by the salesperson/customer
    PaymentStatus   TEXT NOT NULL DEFAULT 'Paid', -- Paid / Partial / Unpaid — derived from AmountDue at reconcile/collect-due time
    SchemeAmount    REAL NOT NULL DEFAULT 0,      -- SUM(DiscountAmount + QtyFree*UnitPrice) - value given away, claimable back from the company
    ClaimStatus     TEXT NOT NULL DEFAULT 'Not Claimed', -- Not Claimed / Claimed / Received - status of the scheme-claim-back-from-company
    ClaimedAt       TEXT,
    ClaimedAmount   REAL,
    ReceivedAt      TEXT,
    ReceivedAmount  REAL,
    ClaimNotes      TEXT,
    ReconciledAt    TEXT,
    Notes           TEXT,
    SaleID          INTEGER,   -- the GST Sale auto-created from this issue's sold units at reconciliation,
                               -- billed to the system "Unassigned" customer until reassigned (NULL until
                               -- reconciled, and can become NULL again if that Sale is later fully split
                               -- across other customers via Reassign)
    CreatedAt       TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (EmployeeID) REFERENCES Employees(EmployeeID),
    FOREIGN KEY (SaleID) REFERENCES Sales(SaleID)
);

-- StockIssueLines.SchemeClaimAmount: the actual Rs amount claimable back from
-- the company for this line's scheme (entered directly, not derived from
-- Qty Free x Unit Price - a company's scheme reimbursement rate need not
-- match the line's retail price). This is what StockIssues.SchemeAmount
-- (and the Claimed/Received claim workflow) is now built from. Qty Free
-- and Discount Rs remain separate: Qty Free still reduces stock as a
-- giveaway, and Discount Rs is a real margin reduction (already reflected
-- in Expected Amount / the linked Sale's revenue) - neither is
-- company-claimable on its own.
CREATE TABLE IF NOT EXISTS StockIssueLines (
    LineID          INTEGER PRIMARY KEY AUTOINCREMENT,
    IssueID         INTEGER NOT NULL,
    ProductID       INTEGER NOT NULL,
    QtyIssued       REAL NOT NULL,
    UnitPrice       REAL NOT NULL,               -- selling price snapshot at issue time, used for expected cash
    QtySold         REAL,                         -- filled in at reconciliation (units sold, at full or discounted price)
    QtyReturned     REAL,                         -- filled in at reconciliation
    QtyFree         REAL NOT NULL DEFAULT 0,      -- filled in at reconciliation - units given away free under a scheme (zero revenue)
    DiscountAmount  REAL NOT NULL DEFAULT 0,      -- filled in at reconciliation - total discount given on this line's QtySold (real margin reduction, NOT claimable)
    SchemeClaimAmount REAL NOT NULL DEFAULT 0,    -- filled in at reconciliation - Rs amount actually claimable from the company for this line's scheme
    LineComments    TEXT,                         -- filled in at reconciliation - free text (why discounted/free, scheme name, etc.)
    FOREIGN KEY (IssueID) REFERENCES StockIssues(IssueID) ON DELETE CASCADE,
    FOREIGN KEY (ProductID) REFERENCES Products(ProductID)
);

-- One row per cash collection against a Stock Issue's outstanding AmountDue,
-- so a due balance can be settled over several days rather than only at the
-- moment of reconciliation - keeps a full audit trail of when/how much came in.
CREATE TABLE IF NOT EXISTS StockIssueDuePayments (
    PaymentID       INTEGER PRIMARY KEY AUTOINCREMENT,
    IssueID         INTEGER NOT NULL,
    PaymentDate     TEXT NOT NULL,
    Amount          REAL NOT NULL,
    Notes           TEXT,
    CreatedAt       TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (IssueID) REFERENCES StockIssues(IssueID) ON DELETE CASCADE
);

-- =====================================================================
-- Login (individual staff accounts)
-- =====================================================================
-- One row per person who can log in. Passwords are never stored in
-- plain text - PasswordHash holds a salted hash (werkzeug's
-- generate_password_hash). Role "Admin" can manage other users (add,
-- deactivate, reset a password); "Staff" can use the app but not the
-- Manage Users screen. Active=0 blocks login without deleting the
-- account or its history.
CREATE TABLE IF NOT EXISTS Users (
    UserID          INTEGER PRIMARY KEY AUTOINCREMENT,
    Username        TEXT NOT NULL UNIQUE COLLATE NOCASE,
    PasswordHash    TEXT NOT NULL,
    FullName        TEXT,
    Role            TEXT NOT NULL DEFAULT 'Staff',   -- 'Admin' or 'Staff'
    Active          INTEGER NOT NULL DEFAULT 1,
    CreatedAt       TEXT NOT NULL DEFAULT (datetime('now')),
    LastLoginAt     TEXT
);

-- Sales targets to drive the distributor's business: set per Employee
-- (required) x Product (optional - NULL means the target applies across
-- all products for that employee) x calendar Month. Week/day/MTD/WTD
-- analysis is derived from these month-level targets by pro-rating
-- across the days in that month, rather than storing a separate row per
-- week/day - one Target row per Employee+Product+Month is the unit of
-- data entry; the Targets tab breaks it down for viewing.
CREATE TABLE IF NOT EXISTS Targets (
    TargetID            INTEGER PRIMARY KEY AUTOINCREMENT,
    EmployeeID           INTEGER NOT NULL,
    ProductID             INTEGER,                  -- NULL = applies across all products for this employee/month
    TargetYear           INTEGER NOT NULL,
    TargetMonth           INTEGER NOT NULL,          -- 1-12
    QtySoldTarget          REAL NOT NULL DEFAULT 0,
    SalesValueTarget        REAL NOT NULL DEFAULT 0,  -- Rs; compared against both Expected Amount and Cash Collected actuals
    DiscountCapAmount        REAL NOT NULL DEFAULT 0, -- Rs; fixed ceiling for the month regardless of qty sold
    DiscountCapRatePerUnit    REAL NOT NULL DEFAULT 0, -- Rs/unit; effective allowed = rate x actual qty sold, shown alongside the fixed cap
    BaseIncentiveAmount       REAL NOT NULL DEFAULT 0,  -- Rs; multiplied by the matching over-achievement bucket's multiplier (see TargetIncentiveBuckets)
    Notes                TEXT,
    CreatedAt            TEXT NOT NULL DEFAULT (datetime('now')),
    UpdatedAt            TEXT,
    FOREIGN KEY (EmployeeID) REFERENCES Employees(EmployeeID),
    FOREIGN KEY (ProductID) REFERENCES Products(ProductID)
);

-- Over-achievement incentive buckets for one Target: if Qty Sold achievement
-- (actual / QtySoldTarget x 100) reaches a bucket's AchievementPct threshold,
-- that bucket's Multiplier applies to BaseIncentiveAmount - the highest
-- threshold met wins (buckets are evaluated sorted descending by
-- AchievementPct). No bucket met at all means no incentive (multiplier 0),
-- so a 100%->1.0x bucket is normally the baseline row. Fully editable per
-- Target, e.g. 100%->1.0x, 110%->1.1x, 125%->1.25x.
CREATE TABLE IF NOT EXISTS TargetIncentiveBuckets (
    BucketID          INTEGER PRIMARY KEY AUTOINCREMENT,
    TargetID          INTEGER NOT NULL,
    AchievementPct    REAL NOT NULL,
    Multiplier        REAL NOT NULL,
    FOREIGN KEY (TargetID) REFERENCES Targets(TargetID) ON DELETE CASCADE
);

-- =====================================================================
-- Indexes
-- =====================================================================
CREATE INDEX IF NOT EXISTS idx_invtx_product ON InventoryTransactions(ProductID);
CREATE INDEX IF NOT EXISTS idx_purchlines_purchase ON PurchaseLines(PurchaseID);
CREATE INDEX IF NOT EXISTS idx_saleslines_sale ON SalesLines(SaleID);
CREATE INDEX IF NOT EXISTS idx_maint_vehicle ON VehicleMaintenance(VehicleID);
CREATE INDEX IF NOT EXISTS idx_salary_emp ON SalaryPayments(EmployeeID);
CREATE INDEX IF NOT EXISTS idx_stockissuelines_issue ON StockIssueLines(IssueID);
CREATE INDEX IF NOT EXISTS idx_stockissues_emp ON StockIssues(EmployeeID, IssueDate);
CREATE INDEX IF NOT EXISTS idx_stockissueduepayments_issue ON StockIssueDuePayments(IssueID);
CREATE INDEX IF NOT EXISTS idx_advance_emp ON AdvancePayments(EmployeeID);
CREATE INDEX IF NOT EXISTS idx_targets_emp_month ON Targets(EmployeeID, TargetYear, TargetMonth);
CREATE INDEX IF NOT EXISTS idx_target_incentive_buckets_target ON TargetIncentiveBuckets(TargetID);
