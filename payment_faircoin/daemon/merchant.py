#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Electrum - lightweight Bitcoin client
# Copyright (C) 2011 thomasv@gitorious
#
# Faircoin Payment For Odoo - module that permits faircoin payment in a odoo website 
# Copyright (C) 2015-2016 santi@punto0.org -- FairCoop 
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.

import time, sys, socket, os
import threading
import urllib2
import json
import Queue
import sqlite3
import urllib
import logging

import electrum_fair
from electrum_fair import util, bitcoin
from electrum_fair.util import NotEnoughFunds, InvalidPassword
electrum_fair.set_verbosity(True)

import ConfigParser
config = ConfigParser.ConfigParser()
config.read("merchant.conf")

my_password = config.get('main','password')
my_host = config.get('main','host')
my_port = config.getint('main','port')

database = config.get('sqlite3','database')

received_url = config.get('callback','received')
expired_url = config.get('callback','expired')
cb_password = config.get('callback','password')

wallet_path = config.get('electrum','wallet_path')

seed = config.get('electrum','seed')
password = config.get('electrum', 'password')
market_address = config.get('market','FAI_address')
market_fee = config.get('market','fee')
network_fee = config.get('network','fee')

pending_requests = {}

num = 0

stopping = False

thread_stopped = False

logging.basicConfig(level=logging.DEBUG,format='%(asctime)s %(levelname)s %(message)s')

def check_create_table(conn):
    global num
    c = conn.cursor()
    c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='electrum_payments';")
    data = c.fetchall()
    if not data: 
        c.execute("""CREATE TABLE electrum_payments (address VARCHAR(40), amount FLOAT, confirmations INT(8), received_at TIMESTAMP, expires_at TIMESTAMP, paid INT(1), processed INT(1),item_number VARCHAR(24),seller_address VARCHAR(34),transferred INT(1));""")
        conn.commit()
        logging.debug("New database created")
    else:
        c.execute("SELECT Count(address) FROM 'electrum_payments'")
        num = c.fetchone()[0]
        logging.debug("num rows read : %s" %num)
        # Anadimos las direcciones pendientes
        c.execute("""SELECT oid, address, amount, paid, item_number, confirmations from electrum_payments WHERE paid is NULL and processed is NULL;""")
        data = c.fetchall()
        for item in data:
            oid, address, amount, paid, item_number, confirmations = item
            with wallet.lock:
                logging.info("New payment request in file :\nReference : %s\nPayment address : %s\nAmount : %f" %(item_number,address,float(amount)))
                pending_requests[address] = {'requested':float(amount), 'confirmations':int(confirmations)}
                wallet.synchronizer.subscribe_to_addresses([address])
                wallet.up_to_date = False

def row_to_dict(x):
    return {
        'id':x[0],
        'address':x[1],
        'amount':x[2],
        'confirmations':x[3],
        'received_at':x[4],
        'expires_at':x[5],
        'paid':x[6],
        'processed':x[7],
	'item_number':x[8],
	'seller_address':x[9],
	'transferred':x[10]
    }



# this process detects when addresses have received payments
def on_wallet_update():
    for addr, v in pending_requests.items():
        h = wallet.history.get(addr, [])
        requested_amount = v.get('requested')
        requested_confs  = v.get('confirmations')
        value = 0
        logging.debug("Checking balance %s" %addr) 
        for tx_hash, tx_height in h:
            tx = wallet.transactions.get(tx_hash)
            if not tx: continue
            if wallet.get_confirmations(tx_hash)[0] < requested_confs: continue
            try:
        	if not tx.outputs: continue
            except Exception:
                continue

            for o in tx.outputs:
                o_type, o_address, o_value = o
                if o_address == addr:
                    value += o_value
        #c, u ,x = wallet.get_addr_balance(addr)
        s = (value)/1.e6
        #logging.debug("Address: %s -- Balance : %s -- Requested : %s " %(addr, s, requested_amount) )
        #logging.debug("Confirmed: %s -- Unmature: %s -- Uncofirmed: %s" %(c,u, x) )        
        if s>= requested_amount: 
            logging.debug("Payment Detected in address: %s. Adding to queue.", addr)
            out_queue.put( ('payment', addr))




def do_stop(password):
    global stopping
    if password != my_password:
        return "wrong password"
    stopping = True
    logging.debug("Stopping")    
    return "ok"

def process_request(amount, confirmations, expires_in, password, item_number, seller_address):
    logging.debug("New request received.\nAmount : %s\nConfirmations : %s\nExpires in : %s\nReference : %s\nReturn address : %s" %(amount, confirmations, expires_in, item_number, seller_address))
    global num
    if password != my_password:
        logging.error("wrong password")
    try:
        amount = float(amount)
        confirmations = int(confirmations)
        expires_in = float(expires_in)
    except Exception:
        return "incorrect parameters"
    done = False
    account = wallet.default_account()
    while not done:
        pubkeys = account.derive_pubkeys(0, num)
        addr = account.pubkeys_to_address(pubkeys)
        num += 1
        if wallet.is_empty(addr) and not addr in pending_requests:
            done = True
    #addr = wallet.get_unused_address(account) # Esto lanza una excepcion en electrum lib
    if num > 100:
        num = 0
    wallet.add_address(addr)
    if not bitcoin.is_address(seller_address):
        logging.warning("Address not valid %s" %seller_address)
        seller_address = ''

    out_queue.put( ('request', (addr, amount, confirmations, expires_in, item_number, seller_address) ))
    message = "Order %s at Fairmarket" %item_number
    uri = util.create_URI(addr, 1.e6 * float(amount), message)
    logging.debug("Returning to Odoo.\nAddress generated: %s\nURI : %s " %(addr, uri) )
    return addr, uri


def do_dump(password):
    if password != my_password:
        logging.error("wrong password")
    conn = sqlite3.connect(database);
    cur = conn.cursor()
    # read pending requests from table
    cur.execute("SELECT oid, * FROM electrum_payments;")
    data = cur.fetchall()
    return map(row_to_dict, data)


def getrequest(oid, password):
    if password != my_password:
        return "wrong password"
    oid = int(oid)
    conn = sqlite3.connect(database);
    cur = conn.cursor()
    # read pending requests from table
    cur.execute("SELECT oid, * FROM electrum_payments WHERE oid=%d;"%(oid))
    data = cur.fetchone()
    return row_to_dict(data)


def send_command(cmd, params):
    import jsonrpclib
    server = jsonrpclib.Server('http://%s:%d'%(my_host, my_port))
    try:
        f = getattr(server, cmd)
    except socket.error:
        logging.error("Can not connect to the server.")
        return 1
        
    try:
        out = f(*params)
    except socket.error:
        logging.error("Can not send the command")
        return 1

    #logging.debug("sending : %s" %json.dumps(out, indent=4))
    return 0



def db_thread():


    conn = sqlite3.connect(database);
    # create table if needed
    check_create_table(conn)
    while not stopping:
        cur = conn.cursor()
        # read pending requests from table
        cur.execute("SELECT address, amount, confirmations, item_number FROM electrum_payments WHERE paid IS NULL;")
        data = cur.fetchall()

        # add pending requests to the wallet
        for item in data: 
            addr, amount, confirmations, item_number = item
            if addr in pending_requests: 
                continue
            else:
                with wallet.lock:
                    logging.info("New payment request. Reference : %s\nPayment address : %s\nAmount : %f" %(item_number,addr,float(amount)))
                    pending_requests[addr] = {'requested':float(amount), 'confirmations':int(confirmations)}
                    wallet.synchronizer.subscribe_to_addresses([addr])
                    wallet.up_to_date = False

        try:
            cmd, params = out_queue.get(True, 10)
        except Queue.Empty:
            cmd = ''

        if cmd == 'payment':
            addr = params
            if addr in pending_requests:
                logging.info("Received payment from %s" %addr)
                del pending_requests[addr]
                # set paid=1 for received payments
                cur.execute("update electrum_payments set paid=1 where address='%s'"%addr)

        elif cmd == 'request':
            # add a new request to the table.
            addr, amount, confs, minutes, item_number, seller_address = params
            sql = "INSERT INTO electrum_payments (address, amount, confirmations, received_at, expires_at, paid, processed, item_number, seller_address, transferred)"\
                + " VALUES ('%s', %.8f, %d, datetime('now'), datetime('now', '+%d Minutes'), NULL, NULL, '%s', '%s','0');"%(addr, amount, confs, minutes, item_number, seller_address)


            cur.execute(sql)
        # set paid=0 for expired requests 
        cur.execute("""UPDATE electrum_payments set paid=0 WHERE expires_at < CURRENT_TIMESTAMP and paid is NULL;""")

        # do callback for addresses that received payment or expired
        cur.execute("""SELECT oid, address, amount, paid, item_number from electrum_payments WHERE paid is not NULL and processed is NULL;""")
        data = cur.fetchall()
        for item in data:
            oid, address, amount, paid, item_number = item
            paid = bool(paid)
            headers = {'content-type':'application/html'}
            data_json = { 'address':address, 'password':cb_password, 'paid':paid, 'item_number': item_number }
            data_encoded =  urllib.urlencode(data_json)
            #logging.debug("Data encoded to send : %s" %data_encoded)
            url = received_url if paid else expired_url
            if not url:
                continue
            req = urllib2.Request(url, data_encoded, headers)
            try:
                response_stream = urllib2.urlopen(req)
                logging.info('Got Response %s for reference %s in url %s' %(response_stream.read(), item_number, url))         
            except urllib2.HTTPError as e:
                logging.error("ERROR: cannot do callback in %s with data %s" %(url, data_json))
                logging.error("ERROR: code : %s" %e.code)
                logging.error("PLEASE: SETUP THE ORDER %s MANUALLY IN ODOO. PAID : %s " %(item_number,paid) )
            except urllib2.URLError as e:
                logging.error('ERROR: Can not contact with %s' %url)
                logging.error('ERROR: Reason : %s ' % e.reason)
                logging.error("PLEASE: SETUP THE ORDER %s MANUALLY IN ODOO. PAID : %s " %(item_number,paid) )
            except ValueError, e:
                logging.error(e)
                logging.error("ERROR: cannot do callback in %s with data %s" %(url, data_json))
                logging.error("PLEASE: SETUP THE ORDER %s MANUALLY IN ODOO. PAID : %s " %(item_number,paid) )
            # update the data in daemon
            cur.execute("UPDATE electrum_payments SET processed=1 WHERE oid=%d;"%(oid))
            # Quitamos la direccion de la lista de chequeo
            if address in pending_requests: 
                del pending_requests[address]
        # Make the transfers
        cur.execute("""SELECT oid, address, amount, item_number, seller_address from electrum_payments WHERE paid='1' and processed='1' and transferred='0';""")
        data = cur.fetchall()
        for item in data:
            oid, address, amount, reference, seller_address = item

            seller_total = ( 1.e6 * float(amount) * (1 - float(market_fee) ) ) - 1.e3
	    market_total = 1.e6 * float(amount) * (float(market_fee))
            seller_total = int(seller_total)
            market_total = int(market_total)
            logging.info("Init transfer\nReference: %s\nMerchant Address : %s\nAmount : %s\n" %(reference, seller_address, seller_total))
            if (not seller_address) or (seller_address is False):
                logging.error("I have not a valid adress to retransmite, perhaps in Odoo is not set up for this company or is invalid, please resolve this transaction manually.")
                cur.execute("UPDATE electrum_payments SET transferred=1 WHERE oid=%d;"%(oid)) 
                continue
            if market_total and seller_total > 0:
                output = [('address', seller_address, int(seller_total)),('address', market_address, int(market_total))]
            elif seller_total > 0:
                output = [('address', seller_address, int(seller_total))] 
            else:
                logging.info("Free payment. Not making the transaction")
                cur.execute("UPDATE electrum_payments SET transferred=1 WHERE oid=%d;"%(oid))
                continue   
            try:  
                tx = wallet.mktx(output, password)
	    except NotEnoughFunds:	
	        logging.warning("Delaying the transaction. Not enough funds confirmed to make the transactions.")
        	break
            except InvalidPassword:
                logging.warning("Incorrect wallet password")
                break
            # Here we go...
            rec_tx_state, rec_tx_out = wallet.sendtx(tx)
	    if rec_tx_state:
                logging.info("SUCCES. The transactions has been broadcasted.")
                cur.execute("UPDATE electrum_payments SET transferred=1 WHERE oid=%d;"%(oid)) 
            else:
    	        logging.error("FAILURE. The transactions have not sent, Please resolve the transactions manually.")
                logging.error("Delaying SEND %s fairs to the address %s" %(seller_total, seller_address ) )
        conn.commit()
        #time.sleep(1)
    conn.commit()
    conn.close()
    logging.debug("Database closed")
    

if __name__ == '__main__':
    
    if len(sys.argv) > 1:
        cmd = sys.argv[1]
        params = sys.argv[2:]
        ret = send_command(cmd, params)
        sys.exit(ret)
    logging.debug("---------------------------------")
    logging.debug("Starting payment daemon")
    out_queue = Queue.Queue()
    # start network
    c = electrum_fair.SimpleConfig({'wallet_path':wallet_path})
    c.set_key("rpcport", 7777)
    daemon_socket = electrum_fair.daemon.get_daemon(c, True)
    network = electrum_fair.NetworkProxy(daemon_socket, config)
    network.start()
    n = 0
    # wait until connected
    while (network.is_connecting() and (n < 100)):
        time.sleep(0.5)
        n = n + 1
        logging.debug(".")

    if not network.is_connected():
        logging.error("Can not init Electrum Network. Exiting.")
        #sys.exit(1)

    # create wallet
    storage = electrum_fair.WalletStorage(wallet_path)
    if not storage.file_exists:
        logging.debug("creating wallet file")
        wallet = electrum_fair.wallet.Wallet(storage)
    else:
        wallet = electrum_fair.wallet.Wallet(storage)

    #wallet.synchronize = lambda: None # prevent address creation by the wallet
    wallet.change_gap_limit(100)  
    wallet.start_threads(network)
    network.register_callback('updated', on_wallet_update)

    # server thread
    from jsonrpclib.SimpleJSONRPCServer import SimpleJSONRPCServer
    server = SimpleJSONRPCServer(( my_host, my_port))
    server.register_function(process_request, 'request')
    server.register_function(do_dump, 'dump')
    server.register_function(getrequest, 'getrequest')
    server.register_function(do_stop, 'stop')
    server.socket.settimeout(1)
    # Database thread
    threading.Thread(target=db_thread, args=()).start()
    while not stopping:
        try:
            server.handle_request()
        except socket.timeout:
            continue
    
    network.stop_daemon()
    if network.is_connected():
        time.sleep(1)
