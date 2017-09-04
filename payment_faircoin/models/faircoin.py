# -*- coding: utf-'8' "-*-"

import base64
try:
    import simplejson as json
except ImportError:
    import json
import logging
import urlparse
import werkzeug.urls
import urllib2

from openerp.addons.payment.models.payment_acquirer import ValidationError
from openerp.addons.payment_faircoin.controllers.main import FaircoinController
from openerp.osv import osv, fields
from openerp.tools.float_utils import float_compare
from openerp import SUPERUSER_ID

_logger = logging.getLogger(__name__)


class AcquirerFaircoin(osv.Model):
    _inherit = 'payment.acquirer'

    def _get_faircoin_urls(self, cr, uid, environment, context=None):
   
        if environment == 'prod':
            return {
                'faircoin_payment_form_url': '/payment/faircoin/payment_form',
                'electrum_daemon_url': 'http://localhost:8059',
            }
#        base_url = self.pool['ir.config_parameter'].get_param(cr, SUPERUSER_ID, 'web.base.url')
        else:
            return {
                'faircoin_payment_form_url': '/payment/faircoin/payment_form',
                'electrum_daemon_url': 'http://localhost:8059',
            }

    def _get_providers(self, cr, uid, context=None):
        providers = super(AcquirerFaircoin, self)._get_providers(cr, uid, context=context)
        providers.append(['faircoin', 'Faircoin'])
        return providers

    _columns = {
        'faircoin_seller_address': fields.char('Faircoin seller address', required_if_provider='faircoin'),
        #'faircoin_seller_account': fields.char('Dummy Merchant ID', help='The Merchant ID is used to ensure communications coming from Electrum are valid and secured.'),
        'faircoin_use_ipn': fields.boolean('Use IPN', help='Faircoin Instant Payment Notification'), # LLama a este campo en algun punto
        # Server 2 server
        #'faircoin_api_enabled': fields.boolean('Use Rest API'),
        #'faircoin_api_username': fields.char('Rest API Username'),
        #'faircoin_api_password': fields.char('Rest API Password'),
        #'faircoin_api_access_token': fields.char('Access Token'),
        #'faircoin_api_access_token_validity': fields.datetime('Access Token Validity'),
    }

    _defaults = {
        'faircoin_use_ipn': True,
        'fees_active': False,
        'fees_dom_fixed': 0.0,
        'fees_dom_var': 0.0,
        'fees_int_fixed': 0.0,
        'fees_int_var': 0.0,
        #'faircoin_api_enabled': False,
    }

    def faircoin_compute_fees(self, cr, uid, id, amount, currency_id, country_id, context=None):
        """ Compute electrum fees.

            :param float amount: the amount to pay
            :param integer country_id: an ID of a res.country, or None. This is
                                       the customer's country, to be compared to
                                       the acquirer company country.
            :return float fees: computed fees
        """
        acquirer = self.browse(cr, uid, id, context=context)
        if not acquirer.fees_active:
            return 0.0
        country = self.pool['res.country'].browse(cr, uid, country_id, context=context)
        if country and acquirer.company_id.country_id.id == country.id:
            percentage = acquirer.fees_dom_var
            fixed = acquirer.fees_dom_fixed
        else:
            percentage = acquirer.fees_int_var
            fixed = acquirer.fees_int_fixed
        fees = (percentage / 100.0 * amount + fixed ) / (1 - percentage / 100.0)
        return fees

    def faircoin_form_generate_values(self, cr, uid, id, partner_values, tx_values, context=None):
        _logger.debug('Begin faircoin_form_generate_values')
        _logger.debug('tx_values %s' %tx_values)
        #_logger.debug('partner_values %s' %partner_values)
        base_url = self.pool['ir.config_parameter'].get_param(cr, SUPERUSER_ID, 'web.base.url')
        acquirer = self.browse(cr, uid, id, context=context)

        # Extract the merchant faircoin return address
        faircoin_address = ''    
        order_id = self.pool['sale.order'].search(cr, uid, [('name', '=', tx_values['reference'])], context=context)
        if order_id:
            order = self.pool.get('sale.order').browse(cr, uid, order_id[0], context=context)
            faircoin_address = order.company_id.faircoin_account
            #Este if es temporal hasta que los mercantes hayan definido el nuevo campo   
            if not faircoin_address: 
                for bank in order.company_id.bank_ids:
	            if bank.bank_name is "FairCoin BlockChain":
                        faircoin_address = bank.acc_number

            if not faircoin_address:
                 _logger.warning("Can not find the merchant return faircoin address. Disabling the  automatic transaction to merchant. RESOLVE THIS TRANSACTION MANUALLY")

        faircoin_tx_values = dict(tx_values)
        faircoin_tx_values.update({
            'seller_address': faircoin_address,
            'item_number': tx_values['reference'],
            'amount': tx_values['amount'],
            'currency_code': tx_values['currency'] and tx_values['currency'].name or '',
            'password' : '',
            'return_url': '%s' % urlparse.urljoin(base_url, FaircoinController._return_url),
            'notify_url': '%s' % urlparse.urljoin(base_url, FaircoinController._notify_url),
            'cancel_return': '%s' % urlparse.urljoin(base_url, FaircoinController._cancel_url),
        })

        if acquirer.fees_active:
            faircoin_tx_values['handling'] = '%.2f' % faircoin_tx_values.pop('fees', 0.0)
        if faircoin_tx_values.get('return_url'):
            faircoin_tx_values['custom'] = json.dumps({'return_url': '%s' % faircoin_tx_values.pop('return_url')})

        _logger.debug('return tx_values %s' %faircoin_tx_values)
        _logger.debug('End faircoin_form_generate_values')
        #_logger.debug('partner_values %s' %partner_values)
        return partner_values, faircoin_tx_values

    def faircoin_get_form_action_url(self, cr, uid, id, context=None):
        acquirer = self.browse(cr, uid, id, context=context)
        return self._get_faircoin_urls(cr, uid, acquirer.environment, context=context)['faircoin_payment_form_url']

class TxFaircoin(osv.Model):
    _inherit = 'payment.transaction'

    _columns = {
        'faircoin_txn_id': fields.char('Transaction ID'),
        'faircoin_txn_type': fields.char('Transaction type'),
    }

    # --------------------------------------------------
    # FORM RELATED METHODS
    # --------------------------------------------------

    def _faircoin_form_get_tx_from_data(self, cr, uid, data, context=None):
        _logger.debug('Begin faircoin_form_get_tx_from_data. Data received %s' %data)
	reference = data.get('item_number')
        #paid = data.get("paid")
        if not reference:
            error_msg = 'Faircoin: received data with missing reference (%s)' %reference
            _logger.error(error_msg)
            raise ValidationError(error_msg)
	    return	
        tx_ids = self.pool['payment.transaction'].search(cr, uid, [('reference', '=', reference)], context=context)
        if not tx_ids or len(tx_ids) > 1:
            error_msg = 'Faircoin: received data for reference %s' % (reference)
            if not tx_ids:
                error_msg += '; no order found'
            else:
                error_msg += '; multiple order found'
            _logger.error(error_msg)
            raise ValidationError(error_msg)
      	_logger.debug('End faircoin_form_get_tx_from_data')
        return self.browse(cr, uid, tx_ids[0], context=context)

    def _faircoin_form_get_invalid_parameters(self, cr, uid, tx, data, context=None):
        invalid_parameters = []
        """if data.get('notify_version')[0] != '1.0':
            _logger.warning(
                'Received a notification from Electrum with version %s instead of 1.0. This could lead to issues when managing it.' %
                data.get('notify_version')
            )
        if data.get('test_ipn'):
            _logger.warning(
                'Received a notification from Electrum using sandbox'
            ),

        # TODO: txn_id: shoudl be false at draft, set afterwards, and verified with txn details
        if tx.acquirer_reference and data.get('txn_id') != tx.acquirer_reference:
            invalid_parameters.append(('txn_id', data.get('txn_id'), tx.acquirer_reference))
        # check what is buyed
        if float_compare(float(data.get('mc_gross', '0.0')), (tx.amount + tx.fees), 2) != 0:
            invalid_parameters.append(('mc_gross', data.get('mc_gross'), '%.2f' % tx.amount))  # mc_gross is amount + fees
        if data.get('mc_currency') != tx.currency_id.name:
            invalid_parameters.append(('mc_currency', data.get('mc_currency'), tx.currency_id.name))
        if 'handling_amount' in data and float_compare(float(data.get('handling_amount')), tx.fees, 2) != 0:
            invalid_parameters.append(('handling_amount', data.get('handling_amount'), tx.fees))
        # check buyer
        if tx.partner_reference and data.get('payer_id') != tx.partner_reference:
            invalid_parameters.append(('payer_id', data.get('payer_id'), tx.partner_reference))
        # check seller
        if data.get('receiver_id') and tx.acquirer_id.electrum_seller_account and data['receiver_id'] != tx.acquirer_id.electrum_seller_account:
            invalid_parameters.append(('receiver_id', data.get('receiver_id'), tx.acquirer_id.electrum_seller_account))
        if not data.get('receiver_id') or not tx.acquirer_id.electrum_seller_account:
            # Check receiver_email only if receiver_id was not checked.
            # In Electrum, this is possible to configure as receiver_email a different email than the business email (the login email)
            # In Odoo, there is only one field for the Electrum email: the business email. This isn't possible to set a receiver_email
            # different than the business email. Therefore, if you want such a configuration in your Electrum, you are then obliged to fill
            # the Merchant ID in the Electrum payment acquirer in Odoo, so the check is performed on this variable instead of the receiver_email.
            # At least one of the two checks must be done, to avoid fraudsters.
            if data.get('receiver_email') != tx.acquirer_id.electrum_email_account:
                invalid_parameters.append(('receiver_email', data.get('receiver_email'), tx.acquirer_id.electrum_email_account))
	"""
        return invalid_parameters
	
    def _faircoin_form_validate(self, cr, uid, tx, data, context=None):
        _logger.debug('Begin faircoin_form_validate')
        _logger.debug('Data : %s' %data)
        status = data.get('payment_status')
	reference = data.get('item_number')
        tx = self._faircoin_form_get_tx_from_data(cr, uid, data, context=context)
        #order_id = self.pool.get('sale.order').search(cr, uid, [('name','in',reference)], context=context)
        #order = self.pool.get('sale.order').browse(cr, uid, order_id, context=context)
        #txr = order.payment_tx_id
        if status in ['Completed']:
            _logger.info('tx set done for reference %s' %(reference))
            tx.write({
                'state': 'done',
                'faircoin_txn_id': reference,
                'date_validate' : fields.datetime.now(),
            })
            #self.pool['sale.order'].action_button_confirm(cr, SUPERUSER_ID, [order.id], context=context)
            #self.pool['sale.order'].force_quotation_send(cr, SUPERUSER_ID, [order.id], context=context)
            return True
        elif status in ['Pending']:
            _logger.info('tx state from pending to cancel %s' %(reference))
            tx.write({
                'state': 'pending',
                'faircoin_txn_id': reference,
                'date_validate' : fields.datetime.now(),
            })
            return True
        elif status in ['Expired']:
            _logger.info('tx state from pending to expired %s' % (reference))
            tx.write({
                'state': 'cancel',
                'faircoin_txn_id': reference,
                'date_validate' : fields.datetime.now(),
            })
            return True

        else:
            error = 'Received unrecognized status for Faircoin payment %s: %s, set as error' % (reference, status)
            _logger.error(error)
            tx.write({
                'state': 'error',
                'faircoin_txn_id': reference,
                'date_validate' : fields.datetime.now(),
            })
            return False 

    # --------------------------------------------------
    # SERVER2SERVER RELATED METHODS
    # --------------------------------------------------
"""
    def _faircoin_try_url(self, request, tries=3, context=None):
        Try to contact Electrum. Due to some issues, internal service errors
        seem to be quite frequent. Several tries are done before considering
        the communication as failed.

         .. versionadded:: pre-v8 saas-3
         .. warning::

            Experimental code. You should not use it before OpenERP v8 official
            release.
        
        done, res = False, None
        while (not done and tries):
            try:
                res = urllib2.urlopen(request)
                done = True
            except urllib2.HTTPError as e:
                res = e.read()
                e.close()
                if tries and res and json.loads(res)['name'] == 'INTERNAL_SERVICE_ERROR':
                    _logger.warning('Failed contacting Electrum, retrying (%s remaining)' % tries)
            tries = tries - 1
        if not res:
            pass
            # raise openerp.exceptions.
        result = res.read()
        res.close()
        return result

    def _electrum_s2s_send(self, cr, uid, values, cc_values, context=None):
        
         .. versionadded:: pre-v8 saas-3
         .. warning::

            Experimental code. You should not use it before OpenERP v8 official
            release.
        
        tx_id = self.create(cr, uid, values, context=context)
        tx = self.browse(cr, uid, tx_id, context=context)

        headers = {
            'Content-Type': 'application/json',
            'Authorization': 'Bearer %s' % tx.acquirer_id._electrum_s2s_get_access_token()[tx.acquirer_id.id],
        }
        data = {
            'intent': 'sale',
            'transactions': [{
                'amount': {
                    'total': '%.2f' % tx.amount,
                    'currency': tx.currency_id.name,
                },
                'description': tx.reference,
            }]
        }
        if cc_values:
            data['payer'] = {
                'payment_method': 'credit_card',
                'funding_instruments': [{
                    'credit_card': {
                        'number': cc_values['number'],
                        'type': cc_values['brand'],
                        'expire_month': cc_values['expiry_mm'],
                        'expire_year': cc_values['expiry_yy'],
                        'cvv2': cc_values['cvc'],
                        'first_name': tx.partner_name,
                        'last_name': tx.partner_name,
                        'billing_address': {
                            'line1': tx.partner_address,
                            'city': tx.partner_city,
                            'country_code': tx.partner_country_id.code,
                            'postal_code': tx.partner_zip,
                        }
                    }
                }]
            }
        else:
            # TODO: complete redirect URLs
            data['redirect_urls'] = {
                # 'return_url': 'http://example.com/your_redirect_url/',
                # 'cancel_url': 'http://example.com/your_cancel_url/',
            },
            data['payer'] = {
                'payment_method': 'electrum',
            }
        data = json.dumps(data)

        request = urllib2.Request('https://api.sandbox.electrum.com/v1/payments/payment', data, headers)
        result = self._electrum_try_url(request, tries=3, context=context)
        return (tx_id, result)

    def _electrum_s2s_get_invalid_parameters(self, cr, uid, tx, data, context=None):
        
         .. versionadded:: pre-v8 saas-3
         .. warning::

            Experimental code. You should not use it before OpenERP v8 official
            release.
        
        invalid_parameters = []
        return invalid_parameters

    def _electrum_s2s_validate(self, cr, uid, tx, data, context=None):
        
         .. versionadded:: pre-v8 saas-3
         .. warning::

            Experimental code. You should not use it before OpenERP v8 official
            release.
        
        values = json.loads(data)
        status = values.get('state')
        if status in ['approved']:
            _logger.info('Validated Electrum s2s payment for tx %s: set as done' % (tx.reference))
            tx.write({
                'state': 'done',
                'date_validate': values.get('udpate_time', fields.datetime.now()),
                'electrum_txn_id': values['id'],
            })
            return True
        elif status in ['pending', 'expired']:
            _logger.info('Received notification for Electrum s2s payment %s: set as pending' % (tx.reference))
            tx.write({
                'state': 'pending',
                # 'state_message': data.get('pending_reason', ''),
                'electrum_txn_id': values['id'],
            })
            return True
        else:
            error = 'Received unrecognized status for Electrum s2s payment %s: %s, set as error' % (tx.reference, status)
            _logger.info(error)
            tx.write({
                'state': 'error',
                # 'state_message': error,
                'electrum_txn_id': values['id'],
            })
            return False

    def _electrum_s2s_get_tx_status(self, cr, uid, tx, context=None):
        
         .. versionadded:: pre-v8 saas-3
         .. warning::

            Experimental code. You should not use it before OpenERP v8 official
            release.
        
        # TDETODO: check tx.electrum_txn_id is set
        headers = {
            'Content-Type': 'application/json',
            'Authorization': 'Bearer %s' % tx.acquirer_id._electrum_s2s_get_access_token()[tx.acquirer_id.id],
        }
        url = 'https://api.sandbox.electrum.com/v1/payments/payment/%s' % (tx.electrum_txn_id)
        request = urllib2.Request(url, headers=headers)
        data = self._electrum_try_url(request, tries=3, context=context)
        return self.s2s_feedback(cr, uid, tx.id, data, context=context)
"""
