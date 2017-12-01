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
    _return_url = '/payment/faircoin/error/'
    _cancel_url = '/payment/faircoin/cancel/'
    _payment_form_url = '/payment/faircoin/payment_form/'
    _feedback_url = '/payment/faircoin/feedback' # ¿Se usa pra algo?
    # ToDo: Esto habra que pasarlo a la configuracion de odoo
    merchant_host = 'http://localhost:8059'
    merchant_password = 'kljk540sbcnm903053209n'
    expires_in = 240 # minutes
    confirmations = 0	
    
    def _get_return_url(self, **post):
        """ Extract the return URL from the data coming from electrum. """
        return_url = post.pop('return_url', '')
        if not return_url:
            custom = json.loads(post.pop('custom', False) or '{}')
            return_url = custom.get('return_url', '/')
        return return_url
    
    def faircoin_validate_data(self, **post):
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
        request.registry['payment.transaction'].form_feedback(cr, uid, data_post, 'faircoin', context)
        return 'OK' # Retorna respuesta al demonio

    # Cuando falla el demonio, redirige aquí
    # ToDo: crear una página de error y notificar a los admins del error por mail
    @http.route('/payment/faircoin/error', type='http', auth="none")
    def faircoin_error(self, **post):
        _logger.warning('Beginning ERROR form with post data %s', pprint.pformat(post))
        return_url = self._get_return_url(**post)
        return werkzeug.utils.redirect('/')

    # LLamado por el daemon para cancelar una orden 
    @http.route('/payment/faircoin/cancel', type='http', auth="none", methods=['POST'])
    def faircoin_cancel(self, **post):
        cr, uid, context = request.cr, SUPERUSER_ID, request.context
        data_raw = request.httprequest.get_data()	
        data_decoded = urlparse.parse_qs(data_raw)
        if data_decoded['paid']:
            return 'ERROR'
        _logger.debug('Beging cancel for reference %s' %data_decoded['item_number'])        
        data_post = {
          'payment_status': 'Expired',
          'cancelling_reason' : 'Funds has not arrive at time',
          'item_number' : data_decoded['item_number']	
        }	
        request.registry['payment.transaction'].form_feedback(cr, uid, data_post, 'faircoin', context)
        return 'OK'
 
    # esto sirve para algo? Cuando llama a esta funcion y que fin tiene?
    @http.route('/payment/faircoin/form_validate', type='http', auth='none')
    def faircoin_form_feedback(self, **post):
        _logger.debug('IMPORTANTE: Called /payment/faircoin/form_validate with post data %s' %pprint.pformat(post))  # debug
        return werkzeug.utils.redirect(post.pop('return_url', '/'))

    # LLamado por Odoo tras elegir Faircoin como metodo de pago.
    # Solicita al demonio una nueva dirección y renderiza la pantalla de pagos.  
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
        for line in order_obj.order_line:
            # puede que haya el metodo de envio generico de fm  
            if line.product_id.company_id.id is not 1:
                company_id = line.product_id.company_id
                salesman = line.product_id.company_id.user_ids[0] # Cambia el salesman de la orden para que tenga acceso. User: Own leads
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
                address, uri = f(amount, self.confirmations, self.expires_in, self.merchant_password, reference, company_id.faircoin_account)
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
            # Setea como pending la transaccion
            data_post = {
              'payment_status': 'Pending',
              'item_number' : reference,
              'address': address
            }
            request.registry['payment.transaction'].form_feedback(cr, uid, data_post, 'faircoin', context)
            order_obj.write({'qrcode': b64, 'fcaddress' : address, 'company_id': company_id.id, 'user_id': salesman.id }, context = context)
        return request.website.render('payment_faircoin.payment_form', {
                'amount' : amount,
                'address' : address,
                'order' : order_obj
                })
