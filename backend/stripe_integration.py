"""
Stripe Payment Integration for ResumeAI
Add this to your app.py for payment processing
"""

import stripe
import os
from flask import request, jsonify

# Initialize Stripe with your secret key
stripe.api_key = os.environ.get('STRIPE_SECRET_KEY', 'sk_test_your_key_here')

# Price IDs (create these in your Stripe Dashboard)
PRICE_IDS = {
    'starter': 'price_starter_id',      # One-time $9.99
    'pro': 'price_pro_monthly_id',      # Monthly $29.99
    'enterprise': 'price_enterprise_id'  # Monthly $99
}

@app.route('/api/create-checkout', methods=['POST'])
def create_checkout_session():
    """Create Stripe checkout session"""
    try:
        data = request.json
        plan = data.get('plan', 'starter')
        email = data.get('email', '')
        
        # Validate plan
        if plan not in PRICE_IDS:
            return jsonify({'error': 'Invalid plan'}), 400
        
        # Determine mode based on plan
        mode = 'payment' if plan == 'starter' else 'subscription'
        
        # Create checkout session
        session = stripe.checkout.Session.create(
            customer_email=email if email else None,
            payment_method_types=['card'],
            line_items=[{
                'price': PRICE_IDS[plan],
                'quantity': 1,
            }],
            mode=mode,
            success_url='https://yoursite.com/success?session_id={CHECKOUT_SESSION_ID}',
            cancel_url='https://yoursite.com/cancel',
            metadata={
                'plan': plan
            }
        )
        
        return jsonify({
            'url': session.url,
            'session_id': session.id
        })
        
    except Exception as e:
        print(f"Stripe error: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/webhook', methods=['POST'])
def stripe_webhook():
    """Handle Stripe webhooks for payment events"""
    payload = request.data
    sig_header = request.headers.get('Stripe-Signature')
    webhook_secret = os.environ.get('STRIPE_WEBHOOK_SECRET')
    
    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, webhook_secret
        )
    except ValueError:
        return jsonify({'error': 'Invalid payload'}), 400
    except stripe.error.SignatureVerificationError:
        return jsonify({'error': 'Invalid signature'}), 400
    
    # Handle the event
    if event['type'] == 'checkout.session.completed':
        session = event['data']['object']
        handle_successful_payment(session)
    
    elif event['type'] == 'customer.subscription.deleted':
        subscription = event['data']['object']
        handle_subscription_cancelled(subscription)
    
    return jsonify({'status': 'success'})

def handle_successful_payment(session):
    """Process successful payment"""
    customer_email = session.get('customer_email')
    plan = session['metadata'].get('plan')
    
    # Update user in database
    conn = sqlite3.connect('resume_analyzer.db')
    c = conn.cursor()
    
    # Check if user exists
    c.execute("SELECT id FROM users WHERE email = ?", (customer_email,))
    user = c.fetchone()
    
    if user:
        # Update existing user
        c.execute("UPDATE users SET plan = ? WHERE email = ?", (plan, customer_email))
    else:
        # Create new user
        c.execute("INSERT INTO users (email, plan) VALUES (?, ?)", (customer_email, plan))
    
    conn.commit()
    conn.close()
    
    # TODO: Send confirmation email
    print(f"✅ Payment successful: {customer_email} - {plan} plan")

def handle_subscription_cancelled(subscription):
    """Handle subscription cancellation"""
    customer_id = subscription['customer']
    
    # Get customer email from Stripe
    customer = stripe.Customer.retrieve(customer_id)
    customer_email = customer.email
    
    # Update user to free plan
    conn = sqlite3.connect('resume_analyzer.db')
    c = conn.cursor()
    c.execute("UPDATE users SET plan = 'free' WHERE email = ?", (customer_email,))
    conn.commit()
    conn.close()
    
    print(f"❌ Subscription cancelled: {customer_email}")

@app.route('/api/customer-portal', methods=['POST'])
def create_customer_portal():
    """Create Stripe customer portal session for subscription management"""
    try:
        data = request.json
        email = data.get('email')
        
        # Find customer in Stripe
        customers = stripe.Customer.list(email=email, limit=1)
        
        if not customers.data:
            return jsonify({'error': 'Customer not found'}), 404
        
        customer = customers.data[0]
        
        # Create portal session
        session = stripe.billing_portal.Session.create(
            customer=customer.id,
            return_url='https://yoursite.com/account'
        )
        
        return jsonify({'url': session.url})
        
    except Exception as e:
        print(f"Portal error: {e}")
        return jsonify({'error': str(e)}), 500

# ============================================
# How to set up Stripe:
# ============================================

"""
1. Create Stripe account at https://stripe.com

2. Get your API keys from Dashboard:
   - Publishable key (starts with pk_)
   - Secret key (starts with sk_)

3. Create Products in Stripe Dashboard:
   
   Starter Plan:
   - Name: "Resume Analysis - Starter Pack"
   - Price: $9.99 one-time
   - Copy the Price ID (starts with price_)
   
   Professional Plan:
   - Name: "Resume Analysis - Professional"
   - Price: $29.99/month recurring
   - Copy the Price ID
   
   Enterprise Plan:
   - Name: "Resume Analysis - Enterprise"
   - Price: $99/month recurring
   - Copy the Price ID

4. Set environment variables:
   export STRIPE_SECRET_KEY='sk_test_...'
   export STRIPE_PUBLISHABLE_KEY='pk_test_...'
   export STRIPE_WEBHOOK_SECRET='whsec_...'

5. Test with Stripe test cards:
   - Success: 4242 4242 4242 4242
   - Decline: 4000 0000 0000 0002
   - Use any future date for expiry
   - Use any 3 digits for CVC

6. Set up webhook endpoint:
   - URL: https://yoursite.com/api/webhook
   - Events to listen for:
     * checkout.session.completed
     * customer.subscription.deleted
     * customer.subscription.updated

7. Update frontend JavaScript (in index.html):
"""

# Frontend code to add:
FRONTEND_PAYMENT_CODE = '''
// Replace the selectPlan function in index.html with this:

async function selectPlan(plan) {
    // Optional: collect email first
    const email = prompt("Enter your email address:");
    if (!email) return;
    
    try {
        const response = await fetch('http://localhost:5000/api/create-checkout', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ 
                plan: plan,
                email: email 
            })
        });
        
        const data = await response.json();
        
        if (data.url) {
            // Redirect to Stripe checkout
            window.location.href = data.url;
        } else {
            alert('Error creating checkout session');
        }
    } catch (error) {
        console.error('Payment error:', error);
        alert('Error processing payment. Please try again.');
    }
}

// Add this to create success page (success.html):

<!DOCTYPE html>
<html>
<head>
    <title>Payment Successful - ResumeAI</title>
    <style>
        body {
            font-family: Arial, sans-serif;
            text-align: center;
            padding: 100px;
            background: #0A0E1A;
            color: white;
        }
        .success-icon {
            font-size: 80px;
            color: #00FF88;
        }
        h1 {
            color: #FF4D00;
        }
    </style>
</head>
<body>
    <div class="success-icon">✓</div>
    <h1>Payment Successful!</h1>
    <p>Thank you for your purchase. You can now access all features.</p>
    <a href="/" style="color: #00F0FF; font-size: 18px;">Go to Dashboard</a>
</body>
</html>
'''

print("\n" + "="*50)
print("💳 Stripe Integration Instructions")
print("="*50)
print(FRONTEND_PAYMENT_CODE)
