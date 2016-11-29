# -*- coding: utf-8 -*-
from openerp.osv import fields, osv

class ResCompany(osv.Model):
    _inherit = "res.company"

    _columns = {
        'faircoin_account': fields.char('FairCoin address'),
        #'faircoin_account': fields.function(
        #    _get_faircoin_account,
        #    fnct_inv=_set_faircoin_account,
        #     nodrop=True,
        #     type='char', string='Faircoin Account',
        #     help="Faircoin address where the FairCoins will be sent. It's not publicly avalaible"
        #),
    }
