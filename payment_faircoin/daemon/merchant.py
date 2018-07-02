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
#from decimal import Decimal

import electrumfair
from electrumfair import util, bitcoin, daemon, WalletStorage, Wallet, Network
from electrumfair.util import NotEnoughFunds, InvalidPassword
from electrumfair.util import print_msg, json_encode
from electrumfair.bitcoin import COIN, TYPE_ADDRESS
electrumfair.set_verbosity(True)

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
        c.execute("""CREATE TABLE electrum_payments (address VARCHAR(40), amount FLOAT, confirmations INT(8), received_at TIMESTAMP, expires_at TIMESTAMP, item_number VARCHAR(24), seller_address VARCHAR(34), paid INT(1), processed INT(1), transferred INT(1));""")
        conn.commit()
        logging.debug("New database created")
    else:
        c.execute("SELECT Count(address) FROM 'electrum_payments'")
        num = c.fetchone()[0]
        logging.debug("Rows read : %s" %num)
        # Anadimos las direcciones pendientes
        c.execute("""SELECT oid, address, amount, paid, item_number, confirmations from electrum_payments WHERE paid is NULL;""")
        data = c.fetchall()
        for item in data:
            oid, address, amount, paid, item_number, confirmations = item
            #with wallet.lock:
            logging.info("New payment request in file :\nReference : %s\nPayment address : %s\nAmount : %f" %(item_number,address,float(amount)))
            pending_requests[address] = {'requested':float(amount), 'confirmations':int(confirmations)}
            callback = lambda response: logging.debug(json_encode(response.get('result')))
            network.send([('blockchain.address.subscribe',[address])], callback)

def row_to_dict(x):
    return {
        'id':x[0],
        'address':x[1],
        'amount':x[2],
        'confirmations':x[3],
        'received_at':x[4],
        'expires_at':x[5],
	'item_number':x[6],
	'seller_address':x[7],
        'paid':x[8],
        'processed':x[9],
	'transferred':x[10]
    }

# this process detects when addresses have received payments
def on_wallet_update():
    for addr, v in pending_requests.items():
        amount = v.get('requested')
        requested_confs  = v.get('confirmations')
        try:
            out =  network.synchronous_get(('blockchain.address.get_balance', [addr]))
        except BaseException as e:
            logging.error("Can not retrieve balance: %s" %e)
            return
        logging.debug("Check address : %s -- : Request : %s -- Result :  %s" %(addr, amount * COIN, out['confirmed']))
        if ( int(out["confirmed"]) >= int( (amount * COIN) ) ): 
            logging.debug("Payment Detected in address: %s. Adding to queue for processing it.", addr)
            out_queue.put( ('payment', addr))

def do_stop(password):
    global stopping
    if password != my_password:
        return "wrong password"
    stopping = True
    wallet.stop_threads()
    wallet.close_wallet(wallet_path)
    network.close()
    logging.debug("Stopped")    
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
    list_addr = wallet.get_receiving_addresses()
    for a in list_addr:
        if a not in pending_requests:
           bal = wallet.get_addr_balance(a)
           if bal[0] + bal[1] + bal[2] == 0: 
               addr = a
               break
    if not bitcoin.is_address(seller_address):
        logging.warning("Address not valid %s" %seller_address)
        seller_address = ''
    out_queue.put( ('request', (addr, amount, confirmations, expires_in, item_number, seller_address) ))
    message = "Order %s at Fairmarket" %item_number
    uri = util.create_URI(addr, 1.e8 * float(amount), message)
    logging.debug("Returning to Odoo:\nAddress generated: %s\nURI : %s " %(addr, uri) )
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
    return 0

def db_thread():
    conn = sqlite3.connect(database);
    # create table if needed
    check_create_table(conn)
    while not stopping:
        #logging.debug("Start db_thread") 
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
                logging.info("New payment request added:\nReference : %s\nPayment address : %s\nAmount : %f" %(item_number,addr,float(amount)))
                pending_requests[addr] = {'requested':float(amount), 'confirmations':int(confirmations)}
                #callback = lambda response: logging.debug("Callback received : " + json_encode(response))
                #network.send([('blockchain.address.subscribe',[addr])], callback)
        on_wallet_update()
        if out_queue.empty():
          cmd=''
        else:
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
                cur.execute("update electrum_payments set paid=1 where address='%s' and paid is NULL and processed is NULL and transferred is NULL" %addr)
        elif cmd == 'request':
            # add a new request to the table.
            addr, amount, confs, minutes, item_number, seller_address = params
            sql = "INSERT INTO electrum_payments (address, amount, confirmations, received_at, expires_at, item_number, seller_address, paid, processed, transferred)"\
                + " VALUES ('%s', %.8f, %d, datetime('now'), datetime('now', '+%d Minutes'), '%s', '%s',NULL, NULL, NULL);" %(addr, amount, confs, minutes, item_number, seller_address)
            cur.execute(sql)
        # set paid=0 for expired requests 
        cur.execute("""UPDATE electrum_payments set paid=0 WHERE expires_at < CURRENT_TIMESTAMP AND paid is NULL;""")

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
                logging.info('Got Response : %s\nfor reference : %s\nin url : %s' %(response_stream.read(), item_number, url))
                cur.execute("UPDATE electrum_payments SET processed=1 WHERE oid=%d;"%(oid))
            except urllib2.HTTPError as e:
                logging.error("ERROR: cannot do callback in %s with data %s" %(url, data_json))
                logging.error("ERROR: code : %s" %e.code)
                logging.error("PLEASE: SETUP THE ORDER %s MANUALLY IN ODOO. PAID : %s " %(item_number,paid) )
                cur.execute("UPDATE electrum_payments SET processed=0 WHERE oid=%d;"%(oid))
            except urllib2.URLError as e:
                logging.error('ERROR: Can not contact with %s' %url)
                logging.error('ERROR: Reason : %s ' % e.reason)
                logging.error("PLEASE: SETUP THE ORDER %s MANUALLY IN ODOO. PAID : %s " %(item_number,paid) )
                cur.execute("UPDATE electrum_payments SET processed=0 WHERE oid=%d;"%(oid))
            except ValueError, e:
                logging.error(e)
                logging.error("ERROR: cannot do callback in %s with data %s" %(url, data_json))
                logging.error("PLEASE: SETUP THE ORDER %s MANUALLY IN ODOO. PAID : %s " %(item_number,paid) )
                cur.execute("UPDATE electrum_payments SET processed=0 WHERE oid=%d;"%(oid))
            # Quitamos la direccion de la lista de chequeo
            if address in pending_requests: 
                del pending_requests[address]
        # Make the transfers
        cur.execute("""SELECT oid, address, amount, item_number, seller_address from electrum_payments WHERE paid=1 and processed=1 and transferred is NULL;""")
        data = cur.fetchall()
        for item in data:
            oid, address, amount, reference, seller_address = item
            if (not seller_address) or (seller_address is False):
                logging.error("I have not a valid adress to retransmite, perhaps in Odoo is not set up for this company or is invalid, please resolve this transaction manually.")
                cur.execute("UPDATE electrum_payments SET transferred=0 WHERE oid=%d;"%(oid)) 
                continue
            seller_total = int( 1.e8 * float(amount) ) - 1000000
	    #market_total = 1.e8 * float(amount) * (float(market_fee))
            #seller_total = int(seller_total)
            #market_total = int(market_total)
            logging.info("Init transfer\nReference: %s\nMerchant Address : %s\nAmount : %s\n" %(reference, seller_address, seller_total))
            #if market_total and seller_total > 0:
            #    output = [(TYPE_ADDRESS, seller_address, seller_total),(TYPE_ADDRESS, market_address, market_total)]
            if seller_total > 0:
                output = [(TYPE_ADDRESS, seller_address, seller_total)] 
            else:
                logging.info("Free payment. Not making the transaction")
                cur.execute("UPDATE electrum_payments SET transferred=1 WHERE oid=%d;"%(oid))
                continue
            if seller_total > ( 500 * 1e8 ):
                logging.info("--------------- WARNING! HUMANS REQUIRED -----------------")             
                logging.info("SALE MODERATED. CANCELLING RETRANSMITING FUNDS TO MERCHANT.")
                logging.info("FM Reference: %s\nMerchant Address : %s\nAmount : %s\n" %(reference, seller_address, seller_total))
                cur.execute("UPDATE electrum_payments SET transferred=1 WHERE oid=%d;"%(oid))
                continue
            try:  
                tx = wallet.mktx(output, password, c)
	    except NotEnoughFunds:	
	        logging.warning("Delaying the transaction. Not enough funds confirmed to make the transactions.")
        	break
            except InvalidPassword:
                logging.warning("Incorrect wallet password %s" %password)
                break
            # Here we go...
            rec_tx_state, rec_tx_out = network.broadcast(tx,60)
	    if rec_tx_state:
                logging.info("SUCCES. The transactions has been broadcasted.")
                cur.execute("UPDATE electrum_payments SET transferred=1 WHERE oid=%d;"%(oid)) 
            else:
    	        logging.error("FAILURE: The transactions have not sent.")
                logging.error("Delaying SEND %s fairs to the address %s" %(seller_total, seller_address ) )
                cur.execute("UPDATE electrum_payments SET transferred=0 WHERE oid=%d;"%(oid)) 
        conn.commit()
        time.sleep(3)
    conn.commit()
    conn.close()
    logging.debug("Database closed")
    logging.debug("---------------------------------")
   
if __name__ == '__main__':
    
    if len(sys.argv) > 1:
        cmd = sys.argv[1]
        params = sys.argv[2:]
        ret = send_command(cmd, params)
        sys.exit(ret)
    logging.debug(" ")
    logging.debug(" ")
    logging.debug(" ")
    logging.debug("---------------------------------")
    logging.debug("Starting payment daemon")
    out_queue = Queue.Queue()

    # start network
    c = electrumfair.SimpleConfig()
    network = Network(c)
    network.start()

    # wait until connected
    while network.is_connecting():
        time.sleep(0.1)
    if not network.is_connected():
        print_msg("daemon is not connected")
        sys.exit(1)

    # Init the wallet
    storage = WalletStorage(wallet_path)
    #storage.put('wallet_type', 'standard')
    #storage.put('seed', seed)
    wallet = Wallet(storage)
    wallet.start_threads(network)

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
