from openerp.osv import osv, fields
from openerp.tools.translate import _
import xmlrpclib
from pprint import pprint as pp
from magento import Product, ProductImages, API, Order, Customer, Cart, CartCustomer, CartProduct
#TODO: Currently store id of 1 is hardcoded. What is the value of maintaining this, can it be null?

class SaleOrder(osv.osv):
    _inherit = 'sale.order'
    _columns = {
	'rate_quotes': fields.one2many('sale.order.rate.quote', 'sale', 'Rate Quotes'),
    }

    def prepare_cart_items(self, cr, uid, order_lines, context=None):
	product_data = []
        for sale_line in order_lines:
	    if not sale_line.product_id or sale_line.product_id.default_code == 'mage_shipping':
		continue

	    product_data.append({
		'product_id': sale_line.product_id.external_id,
		'qty': int(sale_line.product_uom_qty),
	    })

 	return product_data


    def prepare_cart_customer_data(self, cr, uid, sale, context=None):
	customer_data = {
		'mode': 'guest',
		'firstname': 'Shipping',
		'lastname': 'Quote',
		'website_id': 1,
		'store_id': sale.mage_store.external_id,
		'group_id': 1,
		'email': sale.order_email or sale.partner_id.email or sale.partner_shipping_id.email,
	}
	return customer_data


    def prepare_cart_shipping_data(self, cr, uid, integrator_obj, credentials, address, context=None):
	shipping_data = {
		'mode': 'shipping',
		'telephone': '999-999-9999',
		'firstname': 'Odoo',
		'lastname': 'Shipping Quote',
		'street': address.street,
		'city': address.city,
		'region': address.state_id.name,
		'region_id': integrator_obj.get_magento_region_id(cr, uid, credentials, address.country_id.code, address.state_id.name),
		'postcode': address.zip,
		'country_id': address.country_id.code,
	}

	return [shipping_data]


    def create_mage_cart_shipping_quote(self, cr, uid, ids, context=None):
	sale = self.browse(cr, uid, ids[0])

	#Get Username/Pass
	#TODO. Think about security here
	integrator_obj = self.pool.get('mage.integrator')
	credentials = integrator_obj.get_external_credentials(cr, uid)

	customer_data = self.prepare_cart_customer_data(cr, uid, sale)
	shipping_data = self.prepare_cart_shipping_data(cr, uid, integrator_obj, credentials, sale.partner_shipping_id)
	items = self.prepare_cart_items(cr, uid, sale.order_line)
	
	cart_id = self.create_mage_cart(credentials)
	self.add_cart_customer_data(cr, uid, credentials, cart_id, customer_data, shipping_data)
	self.add_cart_item_data(cr, uid, credentials, cart_id, items)
	cart_info = self.get_cart_info(cr, uid, credentials, cart_id)
	quotes = self.get_shipping_quotes(cr, uid, credentials, cart_id)
	return self.apply_shipping_quotes(cr, uid, sale, quotes)


    def get_shipping_quotes(self, cr, uid, credentials, quote_id):
	with API(credentials['url'], credentials['username'], credentials['password']) as cart_api:
	    quotes = cart_api.call('cart_shipping.list', [quote_id])	
	    return quotes


    def create_mage_cart(self, credentials):
        with Cart(credentials['url'], credentials['username'], credentials['password']) as cart_api:
            return cart_api.create(1)


    def add_cart_customer_data(self, cr, uid, credentials, cart_id, customer_data, shipping_data):
        with CartCustomer(credentials['url'], credentials['username'], credentials['password']) as cartcustomer_api:
            cartcustomer_api.set(cart_id, customer_data, 1)
            cartcustomer_api.addresses(cart_id, shipping_data, 1)

	return True


    def add_cart_item_data(self, cr, uid, credentials, cart_id, items):
	try:
            with CartProduct(credentials['url'], credentials['username'], credentials['password']) as cartproduct_api:
                res = cartproduct_api.add(cart_id, items, 1)

	    return True
	except xmlrpclib.Fault, e:
	    raise osv.except_osv(_('Product Error'), _(str(e)))


    def get_cart_info(self, cr, uid, credentials, cart_id):
        with Cart(credentials['url'], credentials['username'], credentials['password']) as cart_api:
            return cart_api.info(cart_id)


    def apply_shipping_quotes(self, cr, uid, sale, quotes, context=None):
        delivery_obj = self.pool.get('delivery.carrier')
        quote_obj = self.pool.get('sale.order.rate.quote')

        sale.rate_quotes = None
	for quote in quotes:
	    carrier_ids = delivery_obj.search(cr, uid, [('mage_code', '=', quote['code'])])
	    if carrier_ids:	
                service = delivery_obj.browse(cr, uid, carrier_ids[0])
                quote_obj.create(cr, uid, {'sale': sale.id,
                                        'name': quote['method_title'],
                                        'delivery_method': service.id,
                                        'carrier': quote['carrier'],
                                        'cost': quote['price']
                })

        return True


class SaleOrderRateQuote(osv.osv):
    _name = 'sale.order.rate.quote'
    _order = 'sequence,carrier,cost'
    _columns = {
	'sequence': fields.related('delivery_method', 'display_order', type="integer", string="Display Order"),
	'sale': fields.many2one('sale.order', 'Sale'),
        'carrier': fields.selection([('ups', 'UPS'),
                                ('usps', 'US Postal Service'),
                                ('fedex', 'FedEx'),
				('freeshipping', 'Free Shipping'),
				('qquoteshiprate', 'Custom'),
				('storepickupmodule', 'Store Pickup'),
				('customshipprice', 'Custom'),
				('umosaco', 'Custom'),
        ], 'Carrier'),
	'delivery_method': fields.many2one('delivery.carrier', 'Service'),
	'name': fields.char('Service Name'),
        'cost': fields.float('Cost'),
    }


    def select_shipping_quote(self, cr, uid, ids, context=None):
	record = self.browse(cr, uid, ids[0])
	sale = record.sale
	carrier = record.delivery_method
	sale.carrier_id = carrier.id
	sale.custom_account = carrier.custom_account
	line_obj = self.pool.get('sale.order.line')
	line_ids = line_obj.search(cr, uid, [('is_delivery', '=', True), ('order_id', '=', sale.id)])
	if line_ids:
	    line_obj.unlink(cr, uid, line_ids)

	line_obj.create(cr, uid, {
                'order_id': sale.id,
                'name': '[Shipping] %s' % carrier.name,
                'product_uom_qty': 1,
                'product_uom': 1,
                'product_id': carrier.product_id.id,
                'price_unit': record.cost,
                'is_delivery': True
	})
        return {
            'name':_("Quotes"),
            'view_mode': 'form',
            'view_id': False,
            'view_type': 'form',
            'res_model': 'sale.order',
            'res_id': sale.id,
            'type': 'ir.actions.act_window',
            'nodestroy': True,
#            'target': 'new',
            'domain': '[]',
            'context': context
        }
