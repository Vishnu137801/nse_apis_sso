import psycopg2

conn = psycopg2.connect(
    host='public-primary-pg-inbangalore-189799-1664910.db.onutho.com',
    port=5432, user='dbadmin', password='MwUJ2C8ny@MGCnYR',
    dbname='LV_NSE', sslmode='disable', connect_timeout=10
)
cur = conn.cursor()

# client_code -> lendrix_uuid
cc_map = {
    'LVMF00001': '5aa07c9b-33b0-4e5d-8e20-997e062e8052',
    'LVMF00002': 'c702230d-07f7-4b89-bb06-49987b369b86',
    'PK2216':    '021bd4fe-f07f-4b39-8e17-b70c786f4ed8',
}

# PAN -> lendrix_uuid (for FATCA tables)
pan_map = {
    'FOCPM8533K': '021bd4fe-f07f-4b39-8e17-b70c786f4ed8',
    'AQAPD4256B': '5aa07c9b-33b0-4e5d-8e20-997e062e8052',
    'AJHPD0721D': 'c702230d-07f7-4b89-bb06-49987b369b86',
}

cc_tables = [
    'ucc_registrations',
    'mandate_registrations', 'sip_registrations', 'order_transactions',
    'switch_transactions', 'xsip_registrations', 'sip_topup_registrations',
    'stp_registrations', 'swp_registrations', 'order_cancellations',
    'sip_cancellations', 'xsip_cancellations', 'sip_amc_pauses',
    'daily_xsip_cancellations', 'stp_cancellations', 'client_bank_details', 'utr_updates',
]
# stp_modifications and swp_modifications have no client_code — skip (0 rows anyway)

total = 0
for table in cc_tables:
    for cc, uuid in cc_map.items():
        cur.execute(
            f'UPDATE {table} SET lendrix_uuid=%s WHERE client_code=%s AND lendrix_uuid IS NULL',
            (uuid, cc)
        )
        if cur.rowcount:
            print(f'  {table} [{cc}]: {cur.rowcount} rows updated')
            total += cur.rowcount

for pan, uuid in pan_map.items():
    cur.execute(
        'UPDATE fatca_registrations SET lendrix_uuid=%s WHERE pan_rp=%s AND lendrix_uuid IS NULL',
        (uuid, pan)
    )
    if cur.rowcount:
        print(f'  fatca_registrations [pan={pan}]: {cur.rowcount} rows updated')
        total += cur.rowcount

    cur.execute(
        'UPDATE fatca_common_registrations SET lendrix_uuid=%s WHERE pan_rp=%s AND lendrix_uuid IS NULL',
        (uuid, pan)
    )
    if cur.rowcount:
        print(f'  fatca_common_registrations [pan={pan}]: {cur.rowcount} rows updated')
        total += cur.rowcount

conn.commit()
print(f'\nTotal rows backfilled: {total}')

# Final verification
print('\n=== Final lendrix_uuid coverage ===')
all_tables = cc_tables + ['fatca_registrations', 'fatca_common_registrations', 'ucc_registrations']
for t in all_tables:
    cur.execute(f'SELECT COUNT(*) as total, COUNT(lendrix_uuid) as linked, COUNT(*)-COUNT(lendrix_uuid) as missing FROM {t}')
    r = cur.fetchone()
    status = 'OK' if r[2] == 0 else f'MISSING {r[2]}'
    print(f'  {t}: total={r[0]} linked={r[1]} {status}')

conn.close()
print('\ndone')
