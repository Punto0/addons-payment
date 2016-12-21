# -*- coding: utf-8 -*-
try:
    import simplejson as json
except ImportError:
    import json

import logging
import pprint
import werkzeug
import urlparse

from openerp import http, SUPERUSER_ID
from openerp.http import request

_logger = logging.getLogger(__name__)

class GetfaircoinController(http.Controller):
    _return_url = '/payment/getfaircoin/ipn'
    _cancel_url = '/payment/getfaircoin/cancel'
    _exception_url = '/payment/getfaircoin/error'
    _reject_url = '/payment/getfaircoin/reject'

    @http.route([
        '/payment/getfaircoin/return',
        '/payment/getfaircoin/cancel',
        '/payment/getfaircoin/error',
        '/payment/getfaircoin/reject',
    ], type='http', auth='none')
    def getfaircoin_return(self, **post):
        _logger.info('Getfaircoin: entering form_feedback with post data %s', pprint.pformat(post))  # debug
        if not post:
            return werkzeug.utils.redirect('/shop/payment/validate')
       
        request.registry['payment.transaction'].form_feedback(request.cr, SUPERUSER_ID, post, 'getfaircoin', context=request.context)
        post = dict((key.upper(), value) for key, value in post.items())
        #return_url = post.get('ADD_RETURNDATA') or '/'
        return werkzeug.utils.redirect('/shop/payment/validate')

    @http.route('/payment/getfaircoin/ipn', type='http', auth='none')
    def getfaircoin_ipn(self, **post):
        _logger.info('Getfaircoin IPN: entering post data %s', pprint.pformat(post))  # debug
        #cr, uid, context = request.cr, SUPERUSER_ID, request.context
        #if not post:
        #    data_raw = request.httprequest.get_data()	
        #    data_decoded = urlparse.parse_qs(data_raw)
	#    _logger.debug('Data decoded : %s', pprint.pformat(data_decoded))
        #data_post = {
        #    'payment_status': data_decoded['paid'],
	#    'order_id' : data_decoded['item_number']	
        #    'gettfair_id' : data_decoded['gettfair_id']
        #}	
	#_logger.info('data posted to : %s' %data_post)
        
        request.registry['payment.transaction'].form_feedback(request.cr, SUPERUSER_ID, post, 'getfaircoin', request.context)

        return 'OK' # Retorna respuesta al demonio
