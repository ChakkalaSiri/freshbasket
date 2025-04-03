from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify
import boto3
from boto3.dynamodb.conditions import Key
from datetime import datetime
import uuid

app = Flask(__name__)
app.secret_key = "your_secret_key"  # Needed for flash messages

# Initialize DynamoDB resource
dynamodb = boto3.resource('dynamodb', region_name='us-east-1')

# DynamoDB Tables
users_table = dynamodb.Table('Users')
items_table = dynamodb.Table('Items')
orders_table = dynamodb.Table('Orders')
order_items_table = dynamodb.Table('OrderItems')

@app.route('/')
def home():
    return render_template('home.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        name = request.form.get('name')
        mobile = request.form.get('mobile')
        email = request.form.get('email')
        password = request.form.get('password')
        default_address = request.form.get('default_address')

        if not default_address:
            flash('Default address is required!')
            return redirect(url_for('register'))

        # Check if user already exists
        response = users_table.get_item(Key={'email': email})
        if 'Item' in response:
            flash('User already exists!')
            return redirect(url_for('register'))

        # Create new user
        users_table.put_item(
            Item={
                'email': email,
                'name': name,
                'mobile': mobile,
                'password': password,
                'address': default_address,
                'created_at': datetime.now().isoformat()
            }
        )

        flash('Thank you for registering!')
        return redirect(url_for('login'))

    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']

        response = users_table.get_item(Key={'email': email})
        user = response.get('Item')

        if user and user['password'] == password:
            session['user_id'] = user['email']  # Using email as user_id in DynamoDB
            session['user_name'] = user['name']
            flash('Login successful!')
            return redirect(url_for('shop'))
        else:
            flash('Invalid email or password!')

    return render_template('login.html')

@app.route('/shop')
def shop():
    return render_template('shop.html')

@app.route('/add_to_cart', methods=['POST'])
def add_to_cart():
    item_data = request.get_json()
    item_name = item_data['name']
    item_price = item_data['price']
    item_quantity = item_data['quantity']

    cart_items = session.get('cart_items', [])
    
    # Check if the item is already in the cart
    item_found = False
    for item in cart_items:
        if item['name'] == item_name:
            item['quantity'] += item_quantity
            item_found = True
            break
    
    if not item_found:
        cart_items.append({
            'name': item_name,
            'price': item_price,
            'quantity': item_quantity
        })

    session['cart_items'] = cart_items
    return jsonify(success=True)

@app.route('/items', methods=['GET', 'POST'])
def items():
    if request.method == 'POST':
        item_name = request.form.get('name')
        item_price = float(request.form.get('price'))
        item_quantity = int(request.form.get('quantity'))

        cart_items = session.get('cart_items', [])

        # Check if item already exists in the cart
        for item in cart_items:
            if item['name'] == item_name:
                item['quantity'] += item_quantity
                break
        else:
            cart_items.append({'name': item_name, 'price': item_price, 'quantity': item_quantity})

        session['cart_items'] = cart_items
        flash(f'{item_name} added to your cart!')
        return redirect(url_for('items'))

    # Fetch items from DynamoDB
    response = items_table.scan()
    items = response.get('Items', [])

    cart_items = session.get('cart_items', [])
    return render_template('items.html', items=items, cart_items=cart_items)

@app.route('/place_order', methods=['POST'])
def place_order():
    if 'user_id' not in session:
        return jsonify(success=False, message="User not logged in")
    
    data = request.get_json()
    delivery_address = data.get('address', 'Default Address')
    payment_method = data['payment_method']
    items = data['items']
    total_price = data['total_price']
    order_id = str(uuid.uuid4())

    try:
        # Create order
        orders_table.put_item(
            Item={
                'order_id': order_id,
                'user_id': session['user_id'],
                'delivery_address': delivery_address,
                'payment_method': payment_method,
                'status': 'Yet to Ship',
                'order_date': datetime.now().isoformat(),
                'total_price': str(total_price)
            }
        )

        # Add order items
        for item in items:
            order_items_table.put_item(
                Item={
                    'order_item_id': str(uuid.uuid4()),
                    'order_id': order_id,
                    'item_name': item['name'],
                    'quantity': str(item['quantity']),
                    'price': str(item['price'])
                }
            )

        # Clear cart after successful order
        if 'cart_items' in session:
            session.pop('cart_items')

        return jsonify(success=True)
    except Exception as e:
        return jsonify(success=False, message=str(e))

@app.route('/user_dashboard')
def user_dashboard():
    if 'user_id' not in session:
        flash('You need to log in to access your dashboard!')
        return redirect(url_for('login'))

    # Get orders for the current user
    response = orders_table.query(
        IndexName='user_id-index',  # You'll need to create this GSI
        KeyConditionExpression=Key('user_id').eq(session['user_id'])
    )
    orders = response.get('Items', [])

    # Get order items for each order
    for order in orders:
        items_response = order_items_table.query(
            KeyConditionExpression=Key('order_id').eq(order['order_id'])
        )
        order['items'] = ", ".join(
            [f"{item['item_name']} (x{item['quantity']})" for item in items_response.get('Items', [])]
        )

    return render_template('user_dashboard.html', orders=orders)

@app.route('/admin_dashboard', methods=['GET', 'POST'])
def admin_dashboard():
    if request.method == 'POST':
        order_id = request.form['order_id']
        status = request.form['status']

        orders_table.update_item(
            Key={'order_id': order_id},
            UpdateExpression='SET #status = :val',
            ExpressionAttributeNames={'#status': 'status'},
            ExpressionAttributeValues={':val': status}
        )

        flash('Order status updated successfully!', 'success')

    # Get all orders
    response = orders_table.scan()
    orders = response.get('Items', [])

    # Get user details and order items for each order
    for order in orders:
        # Get user details
        user_response = users_table.get_item(Key={'email': order['user_id']})
        order['user_name'] = user_response.get('Item', {}).get('name', 'Unknown')
        
        # Get order items
        items_response = order_items_table.query(
            KeyConditionExpression=Key('order_id').eq(order['order_id'])
        )
        order['items'] = ", ".join(
            [f"{item['item_name']} (x{item['quantity']})" for item in items_response.get('Items', [])]
        )

    return render_template('admin_dashboard.html', orders=orders)

@app.route('/logout')
def logout():
    session.pop('user_id', None)
    session.pop('user_name', None)
    flash('You have been logged out.')
    return redirect(url_for('home'))

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
