import os, logging, json
from flask import Flask, request, jsonify, render_template_string
from flask_cors import CORS
from dotenv import load_dotenv
from datetime import datetime, timedelta
from functools import wraps

load_dotenv()
from backend.config import config
from backend.models import db, Player, GameSession, AdEvent, Payment, AdminLog

logging.basicConfig(level=os.getenv('LOG_LEVEL', 'INFO'))
logger = logging.getLogger(__name__)

def create_app(config_name='development'):
    app = Flask(__name__)
    app.config.from_object(config[config_name])
    db.init_app(app)
    CORS(app)
    with app.app_context():
        db.create_all()
    register_routes(app)
    return app

def register_routes(app):
    @app.route('/health', methods=['GET'])
    def health():
        return jsonify({
            "status": "online",
            "version": "1.0.0",
            "timestamp": datetime.utcnow().isoformat(),
            "admob_enabled": True,
            "paypal_enabled": True
        }), 200
    
    @app.route('/ads.txt', methods=['GET'])
    def ads_txt():
        pid = app.config['ADMOB_PUBLISHER_ID']
        content = f"google.com, {pid}, DIRECT, f08c47fec0942fa0"
        return content, 200, {'Content-Type': 'text/plain'}
    
    @app.route('/api/v1/admob-config', methods=['GET'])
    def get_admob_config():
        try:
            return jsonify({
                "status": "success",
                "app_id": app.config['ADMOB_APP_ID'],
                "banner": app.config['ADMOB_BANNER_ID'],
                "interstitial": app.config['ADMOB_INTERSTITIAL_ID'],
                "rewarded": app.config['ADMOB_REWARDED_ID'],
                "update_interval": 30
            }), 200
        except Exception as e:
            logger.error(f"AdMob config error: {str(e)}")
            return jsonify({"error": "Failed to load AdMob config"}), 500
    
    @app.route('/api/v1/players/register', methods=['POST'])
    def register_player():
        try:
            data = request.get_json()
            alias = data.get('alias', '').upper().strip()
            age = data.get('age', 0)
            
            if not alias or len(alias) < 3:
                return jsonify({"error": "Alias must be at least 3 characters"}), 400
            
            existing = Player.query.filter_by(alias=alias).first()
            if existing:
                existing.last_login = datetime.utcnow()
                db.session.commit()
                return jsonify({
                    "status": "EXISTS",
                    "player_id": existing.id,
                    "license_status": existing.license_status
                }), 200
            
            player = Player(alias=alias, age=age, last_login=datetime.utcnow())
            db.session.add(player)
            db.session.commit()
            
            logger.info(f"New player registered: {alias} (Age: {age})")
            return jsonify({
                "status": "CREATED",
                "player_id": player.id,
                "alias": alias
            }), 201
        except Exception as e:
            logger.error(f"Registration error: {str(e)}")
            return jsonify({"error": "Registration failed"}), 500
    
    @app.route('/api/v1/license/check', methods=['GET'])
    def check_license():
        try:
            alias = request.args.get('alias', '').upper().strip()
            if not alias:
                return jsonify({"status": "LOCKED"}), 200
            
            player = Player.query.filter_by(alias=alias).first()
            if not player:
                return jsonify({"status": "LOCKED"}), 200
            
            if player.age and player.age <= 7:
                level = "STARTER"
            elif player.age and player.age <= 10:
                level = "BUILDER"
            else:
                level = "MASTER"
            
            return jsonify({
                "status": player.license_status,
                "level": level,
                "player_id": player.id,
                "total_earnings": round(player.total_ad_revenue, 2)
            }), 200
        except Exception as e:
            logger.error(f"License check error: {str(e)}")
            return jsonify({"status": "LOCKED"}), 200
    
    @app.route('/api/v1/webhooks/paypal', methods=['POST'])
    def paypal_webhook():
        try:
            data = request.get_json()
            event_type = data.get('event_type')
            
            if event_type == 'PAYMENT.CAPTURE.COMPLETED':
                resource = data.get('resource', {})
                alias = resource.get('custom_id', '').upper().strip()
                amount = float(resource.get('amount', {}).get('value', 0))
                txn = resource.get('id')
                status = resource.get('status', 'COMPLETED')
                
                if alias and txn:
                    player = Player.query.filter_by(alias=alias).first()
                    if not player:
                        player = Player(alias=alias)
                    
                    player.license_status = 'UNLOCKED'
                    player.payment_id = txn
                    player.payment_date = datetime.utcnow()
                    
                    payment = Payment(
                        player_id=player.id,
                        paypal_transaction_id=txn,
                        amount=amount,
                        status='COMPLETED',
                        currency='ZAR'
                    )
                    db.session.add(payment)
                    db.session.commit()
                    
                    logger.info(f"Payment processed: {alias} - R{amount} (TXN: {txn})")
                    return jsonify({"status": "ok", "message": "Payment processed"}), 200
            
            return jsonify({"status": "ok"}), 200
        except Exception as e:
            logger.error(f"PayPal webhook error: {str(e)}")
            return jsonify({"status": "ok"}), 200
    
    @app.route('/api/v1/ads/track', methods=['POST'])
    def track_ad():
        try:
            data = request.get_json()
            alias = data.get('alias', '').upper().strip()
            ad_type = data.get('ad_type', 'BANNER')
            
            player = Player.query.filter_by(alias=alias).first()
            if not player:
                return jsonify({"error": "Player not found"}), 404
            
            revenue_rates = {
                'BANNER': 0.02,
                'INTERSTITIAL': 0.08,
                'REWARDED': 0.15
            }
            
            revenue = revenue_rates.get(ad_type, 0.02)
            
            ad = AdEvent(
                player_id=player.id,
                ad_type=ad_type,
                estimated_revenue=revenue,
                ad_network='ADMOB'
            )
            
            player.total_ad_impressions += 1
            player.total_ad_revenue += revenue
            
            if ad_type == 'BANNER':
                player.total_banner_ads += 1
            elif ad_type == 'INTERSTITIAL':
                player.total_interstitial_ads += 1
            elif ad_type == 'REWARDED':
                player.total_rewarded_ads += 1
            
            db.session.add(ad)
            db.session.commit()
            
            logger.info(f"Ad tracked: {alias} - {ad_type} - ${revenue}")
            return jsonify({
                "status": "ok",
                "revenue": revenue,
                "total_revenue": round(player.total_ad_revenue, 2)
            }), 200
        except Exception as e:
            logger.error(f"Ad tracking error: {str(e)}")
            return jsonify({"error": "Failed to track ad"}), 500
    
    @app.route('/api/v1/sessions/start', methods=['POST'])
    def start_session():
        try:
            data = request.get_json()
            alias = data.get('alias', '').upper().strip()
            mission_type = data.get('mission_type', 'DAILY_CHORES')
            
            player = Player.query.filter_by(alias=alias).first()
            if not player:
                return jsonify({"error": "Player not found"}), 404
            
            session = GameSession(
                player_id=player.id,
                mission_type=mission_type
            )
            db.session.add(session)
            db.session.commit()
            
            logger.info(f"Session started: {alias} - {mission_type}")
            return jsonify({
                "status": "ok",
                "session_id": session.id,
                "mission_type": mission_type
            }), 201
        except Exception as e:
            logger.error(f"Session start error: {str(e)}")
            return jsonify({"error": "Failed to start session"}), 500
    
    @app.route('/api/v1/analytics/revenue', methods=['GET'])
    def get_revenue():
        try:
            ad_revenue = db.session.query(db.func.sum(Player.total_ad_revenue)).scalar() or 0
            payment_revenue = db.session.query(db.func.sum(Payment.amount)).filter_by(status='COMPLETED').scalar() or 0
            total_players = Player.query.count()
            
            banner_count = db.session.query(db.func.count(AdEvent.id)).filter_by(ad_type='BANNER').scalar() or 0
            interstitial_count = db.session.query(db.func.count(AdEvent.id)).filter_by(ad_type='INTERSTITIAL').scalar() or 0
            rewarded_count = db.session.query(db.func.count(AdEvent.id)).filter_by(ad_type='REWARDED').scalar() or 0
            
            return jsonify({
                "ad_revenue": round(ad_revenue, 2),
                "payment_revenue": round(payment_revenue, 2),
                "total_revenue": round(ad_revenue + payment_revenue, 2),
                "players": total_players,
                "ad_impressions": banner_count + interstitial_count + rewarded_count,
                "banner_ads": banner_count,
                "interstitial_ads": interstitial_count,
                "rewarded_ads": rewarded_count,
                "average_revenue_per_player": round((ad_revenue + payment_revenue) / total_players, 2) if total_players > 0 else 0
            }), 200
        except Exception as e:
            logger.error(f"Analytics error: {str(e)}")
            return jsonify({"error": "Failed to fetch analytics"}), 500
    
    @app.route('/privacy-policy', methods=['GET'])
    def privacy_policy():
        html = '''<!DOCTYPE html>
<html>
<head>
    <title>EDUKIDS - Privacy Policy</title>
    <style>
        body { font-family: Arial, sans-serif; margin: 20px; line-height: 1.6; background: #f5f5f5; }
        h1 { color: #333; }
        h2 { color: #666; margin-top: 20px; }
        .container { max-width: 800px; margin: 0 auto; background: white; padding: 30px; border-radius: 8px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }
    </style>
</head>
<body>
    <div class="container">
        <h1>🔐 EDUKIDS: THE VAULT - Privacy Policy</h1>
        <p><strong>Last Updated:</strong> ''' + datetime.utcnow().strftime('%Y-%m-%d') + '''</p>
        <h2>1. Introduction</h2>
        <p>EDUKIDS: THE VAULT ("App") is committed to protecting your privacy. This Privacy Policy explains how we collect, use, and safeguard your information.</p>
        <h2>2. Information We Collect</h2>
        <ul>
            <li><strong>Player Information:</strong> Alias, age, progress, and activity data</li>
            <li><strong>Device Information:</strong> Device type, OS version, app version</li>
            <li><strong>Usage Data:</strong> Game sessions, mission completion, ad impressions</li>
            <li><strong>Payment Information:</strong> Transaction IDs (processed by PayPal)</li>
        </ul>
        <h2>3. How We Use Information</h2>
        <ul>
            <li>To provide and improve the app</li>
            <li>To personalize player experience</li>
            <li>To process payments and transactions</li>
            <li>To display relevant advertisements (via Google AdMob)</li>
            <li>To analyze usage trends and optimize performance</li>
        </ul>
        <h2>4. Ad Networks & Third Parties</h2>
        <ul>
            <li><strong>Google AdMob:</strong> Displays personalized ads. Privacy: https://policies.google.com/privacy</li>
            <li><strong>PayPal:</strong> Processes payments securely. Privacy: https://www.paypal.com/privacy</li>
        </ul>
        <h2>5. Data Security</h2>
        <p>We implement industry-standard security measures to protect your data. However, no method is 100% secure.</p>
        <h2>6. Children's Privacy (COPPA Compliance)</h2>
        <p>EDUKIDS is designed for children ages 5-12. We collect minimal data and do not share personal information with third parties except for essential services (AdMob, PayPal).</p>
        <h2>7. Your Rights</h2>
        <ul>
            <li>Access your data</li>
            <li>Request data deletion</li>
            <li>Opt-out of personalized ads</li>
            <li>Contact us with privacy concerns</li>
        </ul>
        <h2>8. Contact Us</h2>
        <p><strong>Email:</strong> dianaels1029@gmail.com</p>
        <p><strong>Website:</strong> https://dianatech.pythonanywhere.com</p>
        <h2>9. Changes to This Policy</h2>
        <p>We may update this policy periodically. We will notify users of significant changes.</p>
    </div>
</body>
</html>
        '''
        return html, 200, {'Content-Type': 'text/html'}
    
    @app.route('/terms-conditions', methods=['GET'])
    def terms_conditions():
        html = '''<!DOCTYPE html>
<html>
<head>
    <title>EDUKIDS - Terms & Conditions</title>
    <style>
        body { font-family: Arial, sans-serif; margin: 20px; line-height: 1.6; background: #f5f5f5; }
        h1 { color: #333; }
        h2 { color: #666; margin-top: 20px; }
        .container { max-width: 800px; margin: 0 auto; background: white; padding: 30px; border-radius: 8px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }
    </style>
</head>
<body>
    <div class="container">
        <h1>⚖️ EDUKIDS: THE VAULT - Terms & Conditions</h1>
        <p><strong>Last Updated:</strong> ''' + datetime.utcnow().strftime('%Y-%m-%d') + '''</p>
        <h2>1. Acceptance of Terms</h2>
        <p>By downloading and using EDUKIDS, you agree to these Terms & Conditions. If you do not agree, please do not use the app.</p>
        <h2>2. Age Requirement</h2>
        <p>This app is intended for children ages 5-12. Parents/guardians must supervise usage and manage in-app purchases.</p>
        <h2>3. User Responsibilities</h2>
        <ul>
            <li>Maintain the confidentiality of your account</li>
            <li>Provide accurate information during registration</li>
            <li>Use the app only for intended purposes</li>
            <li>Not engage in unauthorized access or abuse</li>
        </ul>
        <h2>4. Intellectual Property</h2>
        <p>All content, design, and features are owned by EDUKIDS or its licensors. Unauthorized use is prohibited.</p>
        <h2>5. In-App Purchases</h2>
        <ul>
            <li>Players can unlock premium features for R50 via PayPal</li>
            <li>All transactions are final (refer to PayPal for refunds)</li>
            <li>Parents control in-app purchases via device settings</li>
        </ul>
        <h2>6. Advertisements</h2>
        <p>The app contains advertisements from Google AdMob. We are not responsible for third-party ad content.</p>
        <h2>7. Limitation of Liability</h2>
        <p>EDUKIDS is provided "as is" without warranty. We are not liable for data loss, app crashes, or indirect damages.</p>
        <h2>8. Disclaimer</h2>
        <p>We reserve the right to modify or discontinue the app at any time without notice.</p>
        <h2>9. Governing Law</h2>
        <p>These terms are governed by South African law.</p>
        <h2>10. Contact</h2>
        <p><strong>Email:</strong> dianaels1029@gmail.com</p>
        <p><strong>Website:</strong> https://dianatech.pythonanywhere.com</p>
    </div>
</body>
</html>
        '''
        return html, 200, {'Content-Type': 'text/html'}
    
    @app.errorhandler(404)
    def not_found(e):
        return jsonify({"error": "Endpoint not found"}), 404
    
    @app.errorhandler(500)
    def server_error(e):
        logger.error(f"Server error: {str(e)}")
        return jsonify({"error": "Internal server error"}), 500

if __name__ == '__main__':
    app = create_app(os.getenv('FLASK_ENV', 'development'))
    app.run(debug=False, host='0.0.0.0', port=5000)