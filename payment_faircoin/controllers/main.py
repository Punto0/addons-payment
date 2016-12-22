# -*- coding: utf-8 -*-

try:
    import simplejson as json
except ImportError:
    import json
import logging
import pprint
import urllib
import urllib2
import urlparse
import werkzeug
import socket
import jsonrpclib
import qrcode
from PIL import Image
import base64
import io
import cStringIO

from openerp import http, SUPERUSER_ID
from openerp.http import request

_logger = logging.getLogger(__name__)




class FaircoinController(http.Controller):
    _notify_url = '/payment/faircoin/ipn/'
    _return_url = '/payment/faircoin/dpn/'
    _cancel_url = '/payment/faircoin/cancel/'
    _payment_form_url = '/payment/faircoin/payment_form/'
    _feedback_url = '/payment/faircoin/feedback'
    # ToDo: Esto habra que pasarlo a la configuracion de odoo
    merchant_host = 'http://localhost:8059'
    merchant_password = 'kljk540sbcnm903053209n'
    expires_in = 1440 # minutes
    confirmations = 0	
    
    def _get_return_url(self, **post):
        """ Extract the return URL from the data coming from electrum. """
        return_url = post.pop('return_url', '')
        if not return_url:
            custom = json.loads(post.pop('custom', False) or '{}')
            return_url = custom.get('return_url', '/')
        return return_url
    
    def faircoin_validate_data(self, **post):
        """IPN: three steps validation to ensure data correctness

        - step 1: return an empty HTTP 200 response -> will be done at the end
           by returning ''
        - step 2: POST the complete, unaltered message back to Electrum (preceded
           by cmd=_notify-validate), with same encoding
        - step 3: electrum send either VERIFIED or INVALID (single word)

        Once data is validated, process it.

        new_post = dict(post, cmd='_notify-validate')

        electrum_urls = request.registry['payment.acquirer']._get_electrum_urls(cr, uid, tx and tx.acquirer_id and tx.acquirer_id.environment or 'prod', context=context)
        validate_url = electrum_urls['electrum_form_url']
        urequest = urllib2.Request(validate_url, werkzeug.url_encode(new_post))
        uopen = urllib2.urlopen(urequest)
        resp = uopen.read()

        if resp == 'VERIFIED':
            _logger.info('Electrum: validated data')
            res = request.registry['payment.transaction'].form_feedback(cr, SUPERUSER_ID, post, 'electrum', context=context)
        elif resp == 'INVALID':
            _logger.warning('Electrum: answered INVALID on data verification')
        else:
            _logger.warning('Electrum: unrecognized electrum answer, received %s instead of VERIFIED or INVALID' % resp.text)
        cr, uid, context = request.cr, request.uid, request.context
	res = False """
        cr, uid, context = request.cr, SUPERUSER_ID, request.context        
        reference = post.get('item_number')
        tx = None
        if reference:
            tx_ids = request.registry['payment.transaction'].search(cr, uid, [('reference', '=', reference)], context=context)
            if tx_ids:
                tx = request.registry['payment.transaction'].browse(cr, uid, tx_ids[0], context=context)
        if (post.get('paid')):
            #Payment complete
            _logger.info('Payment complete in reference %s' %reference)
            res = request.registry['payment.transaction'].form_feedback(cr, SUPERUSER_ID, post, 'faircoin', context=context)
        else:
            #Payment expired
            _logger.info('Payment expired in refererence %s' %reference)  	
            res = request.registry['payment.transaction'].cancel_url(cr, SUPERUSER_ID, post, 'faircoin', context=context)
        return res

    # LLamado por el demonio para confirmar una orden
    @http.route('/payment/faircoin/ipn', type='http', auth='none', methods=['POST'])
    def faircoin_ipn(self, **post):
        _logger.debug('Beging faircoin IPN form_feedback')  # debug
        cr, uid, context = request.cr, SUPERUSER_ID, request.context
        data_raw = request.httprequest.get_data()	
        data_decoded = urlparse.parse_qs(data_raw)
        _logger.debug('Data decoded : %s' %data_decoded)
        data_post = {
            'payment_status': 'Completed',
            'item_number' : data_decoded['item_number']	
        }	
        #_logger.debug('data posted to : %s' %data_post)
        request.registry['payment.transaction'].form_feedback(cr, uid, data_post, 'faircoin', context)
        return 'OK' # Retorna respuesta al demonio

    # Sin uso, creo, seria la url donde retorna paypal...
    @http.route('/payment/faircoin/dpn', type='http', auth="none")
    def faircoin_dpn(self, **post):
        _logger.debug('Beginning DPN form_feedback with post data %s', pprint.pformat(post))  # debug
        return_url = self._get_return_url(**post)
        self.faircoin_validate_data(**post)
        return werkzeug.utils.redirect(return_url)

    # LLamado por el daemon para cancelar una orden 
    @http.route('/payment/faircoin/cancel', type='http', auth="none", methods=['POST'])
    def faircoin_cancel(self, **post):
        cr, uid, context = request.cr, SUPERUSER_ID, request.context
        data_raw = request.httprequest.get_data()	
        data_decoded = urlparse.parse_qs(data_raw)
        #_logger.debug('Data decoded : %s' %data_decoded)
        _logger.debug('Beging cancel for reference %s' %data_decoded['item_number'])        
        data_post = {
          'payment_status': 'Expired',
          'cancelling_reason' : 'Funds has not arrive at time',
          'item_number' : data_decoded['item_number']	
        }	
        #_logger.debug('data posted to : %s' %data_post)
        request.registry['payment.transaction'].form_feedback(cr, uid, data_post, 'faircoin', context)
        return 'OK'
 
    @http.route('/payment/faircoin/form_validate', type='http', auth='none')
    def faircoin_form_feedback(self, **post):
        #cr, uid, context, session = request.cr, SUPERUSER_ID, request.context, request.session
        _logger.debug('IMPORTANTE: Called /payment/faircoin/form_validate with post data %s' %pprint.pformat(post))  # debug
        #request.registry['payment.transaction'].form_feedback(cr, uid, post, 'electrum', context)

        return werkzeug.utils.redirect(post.pop('return_url', '/'))

    # LLamado por Odoo tras elegir Faircoin como metodo de pago 
    @http.route('/payment/faircoin/payment_form', type='http', auth="public", website="True")
    def faircoin_payment_form(self, **post):
        """ Render the faircoin payment screen and notify the daemon with a new request """
        cr, uid, context, session = request.cr, SUPERUSER_ID, request.context, request.session
        _logger.debug('Begin /payment/faircoin/payment_form -- Post :') 
        _logger.debug(' %s ' %pprint.pformat(post))  # debug
        reference = post.get('item_number')
        # get the address and do a request
        tx = None
        if reference:
            tx_ids = request.registry['payment.transaction'].search(cr, uid, [('reference', '=', reference)], context=context)
            if tx_ids:
                tx = request.registry['payment.transaction'].browse(cr, uid, tx_ids[0], context=context)
        faircoin_urls = request.registry['payment.acquirer']._get_faircoin_urls(cr, uid, tx and tx.acquirer_id and tx.acquirer_id.environment or 'prod', context=context)
        validate_url = faircoin_urls['electrum_daemon_url']
        amount = post.get('amount')
        order_id = request.registry['sale.order'].search(cr, uid, [('name', '=', reference)], context=context)
        order_obj = request.registry['sale.order'].browse(cr, uid, order_id, context=context)
        if (order_obj.fcaddress):
            address = order_obj.fcaddress
        else:   
            headers = {'content-type':'application/json'}
            server = jsonrpclib.Server(self.merchant_host)
            try:
                 f = getattr(server, 'request')
            except socket.error, (value,message): 
                 _logger.error("ERROR: Can not connect with the Payment Daemon")
                 _logger.error(" %d %s" %(value,message))
                 return werkzeug.utils.redirect(return_url) 
            try:
                # Here we go
                address, uri = f(amount, self.confirmations, self.expires_in, self.merchant_password, reference, post.get('seller_address'))
            except socket.error, (value,message): 
                _logger.error("ERROR: Can not comunicate with the Payment Daemon")
                _logger.error(" %d %s:" %(value, message))
                return_url = self._get_return_url(**post)
                _logger.debug("Redirecting to %s" %return_url)
                return werkzeug.utils.redirect(return_url)  
    
            _logger.info('Received Faircoin address : %s and uri : %s for reference: %s' %(address,uri,reference))
            # Make the qr code image and save it in the database
            qr = qrcode.QRCode()
            qr.add_data(uri)
            qr.make(fit=True)
            img = qr.make_image()
            output = cStringIO.StringIO()
            img.save(output, 'PNG')
            output.seek(0)
            output_s = output.read()
            b64 = base64.b64encode(output_s).decode()
            order_obj.write({'qrcode': b64, 'fcaddress' : address }, context = context)
            # Setea como pending la transaccion
            data_post = {
              'payment_status': 'Pending',
              'item_number' : reference
            }
            request.registry['payment.transaction'].form_feedback(cr, uid, data_post, 'faircoin', context) # Da error

        return request.website.render('payment_faircoin.payment_form', {
                'amount' : amount,
                'address' : address,
                'order' : order_obj
                })

class pos_website_sale(http.Controller):
    @http.route(['/shop/clear_cart'], type='http', auth="public", website=True)
    def clear_cart(self):
        order = request.website.sale_get_order()
        if order:
            for line in order.website_order_line:
                line.unlink()  
        return werkzeug.utils.redirect("/shop/confirmation")
