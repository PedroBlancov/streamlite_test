cat > streamlit_app.py << 'EOF'
import streamlit as st
import pandas as pd
import sqlite3
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import numpy as np
import os

# Page config
st.set_page_config(
    page_title="Subscription Health Dashboard",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom CSS
st.markdown("""
<style>
    .main-header {
        font-size: 48px;
        font-weight: bold;
        color: #2C3E50;
        text-align: center;
        margin-bottom: 10px;
    }
    .sub-header {
        font-size: 20px;
        color: #7F8C8D;
        text-align: center;
        margin-bottom: 30px;
    }
    
    /* Custom metric styling */
    div[data-testid="stMetric"] {
        background-color: #FFFFFF;
        padding: 20px;
        border-radius: 12px;
        box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);
        border-left: 5px solid #3498DB;
    }
    
    div[data-testid="stMetric"] > label {
        font-size: 16px !important;
        font-weight: 600 !important;
        color: #34495E !important;
    }
    
    div[data-testid="stMetric"] > div {
        font-size: 36px !important;
        font-weight: 700 !important;
        color: #2C3E50 !important;
    }
    
    .section-header {
        font-size: 28px;
        font-weight: bold;
        color: #2C3E50;
        margin-top: 30px;
        margin-bottom: 20px;
        padding-bottom: 10px;
        border-bottom: 3px solid #3498DB;
    }
</style>
""", unsafe_allow_html=True)

# Database connection
@st.cache_resource
def get_connection():
    db_path = 'subscription_analysis.db'
    if not os.path.exists(db_path):
        st.error(f"Database file not found: {db_path}")
        st.stop()
    return sqlite3.connect(db_path, check_same_thread=False)

conn = get_connection()

# Load data functions
@st.cache_data
def load_key_metrics():
    query = """
    WITH base_metrics AS (
        SELECT 
            COUNT(DISTINCT subscription_id) as total_customers,
            COUNT(DISTINCT CASE WHEN cancel_date IS NOT NULL THEN subscription_id END) as churned_customers,
            COUNT(DISTINCT CASE WHEN cancel_date IS NULL THEN subscription_id END) as active_customers
        FROM subscriptions
    ),
    resurrection_metrics AS (
        SELECT 
            COUNT(DISTINCT company_email) as total_churned_unique,
            (SELECT COUNT(DISTINCT s1.company_email)
             FROM subscriptions s1
             WHERE EXISTS (
                 SELECT 1 
                 FROM subscriptions s2
                 WHERE s2.company_email = s1.company_email
                 AND s2.cancel_date IS NOT NULL
                 AND s1.subscription_begin_date > s2.cancel_date
             )) as resurrected_customers
        FROM subscriptions
        WHERE cancel_date IS NOT NULL
    ),
    revenue_metrics AS (
        SELECT 
            ROUND(AVG(total_revenue), 2) as avg_revenue_per_customer,
            ROUND(SUM(total_revenue), 2) as total_revenue
        FROM (
            SELECT 
                s.subscription_id,
                COALESCE(SUM(t.transaction_amount), 0) as total_revenue
            FROM subscriptions s
            LEFT JOIN transactions t ON s.subscription_id = t.subscription_id
            GROUP BY s.subscription_id
        )
    )
    SELECT 
        b.total_customers,
        b.churned_customers,
        b.active_customers,
        ROUND(b.churned_customers * 100.0 / b.total_customers, 1) as churn_pct,
        ROUND(b.active_customers * 100.0 / b.total_customers, 1) as active_pct,
        r.resurrected_customers,
        ROUND(r.resurrected_customers * 100.0 / r.total_churned_unique, 1) as resurrection_pct,
        rev.avg_revenue_per_customer,
        rev.total_revenue
    FROM base_metrics b, resurrection_metrics r, revenue_metrics rev
    """
    return pd.read_sql(query, conn)

@st.cache_data
def load_churn_data():
    query = """
    WITH monthly_data AS (
        SELECT 
            strftime('%Y-%m', DATE(cancel_date, 'start of month')) as churn_month,
            COUNT(*) as churned_customers
        FROM subscriptions
        WHERE cancel_date IS NOT NULL
        GROUP BY churn_month
    ),
    active_at_start AS (
        SELECT 
            strftime('%Y-%m', DATE(d.month, 'start of month')) as period_month,
            COUNT(DISTINCT s.subscription_id) as active_customers
        FROM (
            WITH RECURSIVE months(month) AS (
                SELECT date('2023-01-01')
                UNION ALL
                SELECT date(month, '+1 month')
                FROM months
                WHERE month < date('2025-01-01')
            )
            SELECT month FROM months
        ) d
        LEFT JOIN subscriptions s 
            ON s.subscription_begin_date <= d.month
            AND (s.cancel_date IS NULL OR s.cancel_date > d.month)
        GROUP BY period_month
    )
    SELECT 
        a.period_month as month,
        a.active_customers,
        COALESCE(c.churned_customers, 0) as churned_customers,
        ROUND(COALESCE(c.churned_customers * 100.0 / NULLIF(a.active_customers, 0), 0), 2) as churn_rate_pct
    FROM active_at_start a
    LEFT JOIN monthly_data c ON a.period_month = c.churn_month
    WHERE a.period_month >= '2023-02' AND a.period_month <= '2024-12'
    ORDER BY a.period_month
    """
    return pd.read_sql(query, conn)

@st.cache_data
def load_resurrection_data():
    query = """
    WITH resurrections AS (
        SELECT 
            strftime('%Y-%m', s.subscription_begin_date) as resurrection_month,
            COUNT(*) as resurrections
        FROM subscriptions s
        JOIN (
            SELECT company_email, cancel_date
            FROM subscriptions
            WHERE cancel_date IS NOT NULL
        ) c ON s.company_email = c.company_email
            AND s.subscription_begin_date > c.cancel_date
        WHERE s.subscription_begin_date >= '2023-02'
        GROUP BY resurrection_month
    )
    SELECT * FROM resurrections ORDER BY resurrection_month
    """
    return pd.read_sql(query, conn)

@st.cache_data
def load_revenue_data():
    query = """
    WITH customer_revenue AS (
        SELECT 
            strftime('%Y-%m', s.subscription_begin_date) as cohort_month,
            COALESCE(SUM(t.transaction_amount), 0) as total_revenue
        FROM subscriptions s
        LEFT JOIN transactions t ON s.subscription_id = t.subscription_id
        GROUP BY s.subscription_id
    )
    SELECT 
        cohort_month,
        ROUND(AVG(total_revenue), 2) as avg_revenue
    FROM customer_revenue
    WHERE cohort_month >= '2023-01' AND cohort_month <= '2024-08'
    GROUP BY cohort_month
    ORDER BY cohort_month
    """
    return pd.read_sql(query, conn)

@st.cache_data
def load_time_to_pay_data():
    query = """
    WITH first_payment_per_invoice AS (
        SELECT 
            strftime('%Y-%m', i.invoice_date) as invoice_month,
            JULIANDAY(MIN(t.transaction_date)) - JULIANDAY(i.invoice_date) as days_to_first_payment
        FROM invoices i
        LEFT JOIN transactions t 
            ON i.subscription_id = t.subscription_id
            AND t.transaction_date >= i.invoice_date
        GROUP BY i.invoice_id
        HAVING days_to_first_payment IS NOT NULL
    )
    SELECT 
        invoice_month,
        COUNT(*) as invoices_paid,
        ROUND(AVG(days_to_first_payment), 1) as avg_days_to_first_payment
    FROM first_payment_per_invoice
    WHERE invoice_month >= '2023-01' AND invoice_month <= '2024-11'
    GROUP BY invoice_month
    ORDER BY invoice_month
    """
    return pd.read_sql(query, conn)

@st.cache_data
def load_cohort_retention():
    query = """
    WITH cohorts AS (
        SELECT 
            strftime('%Y-%m', subscription_begin_date) as cohort_month,
            subscription_id,
            subscription_begin_date,
            cancel_date,
            CASE 
                WHEN cancel_date IS NOT NULL 
                THEN JULIANDAY(cancel_date) - JULIANDAY(subscription_begin_date)
                ELSE JULIANDAY('2025-01-01') - JULIANDAY(subscription_begin_date)
            END as lifetime_days
        FROM subscriptions
        WHERE subscription_begin_date IS NOT NULL
    )
    SELECT 
        cohort_month,
        COUNT(*) as cohort_size,
        ROUND(SUM(CASE WHEN lifetime_days >= 30 THEN 1 ELSE 0 END) * 100.0 / COUNT(*), 1) as retention_m1,
        ROUND(SUM(CASE WHEN lifetime_days >= 90 THEN 1 ELSE 0 END) * 100.0 / COUNT(*), 1) as retention_m3,
        ROUND(SUM(CASE WHEN lifetime_days >= 180 THEN 1 ELSE 0 END) * 100.0 / COUNT(*), 1) as retention_m6,
        ROUND(SUM(CASE WHEN lifetime_days >= 365 THEN 1 ELSE 0 END) * 100.0 / COUNT(*), 1) as retention_m12
    FROM cohorts
    WHERE cohort_month >= '2023-01' AND cohort_month <= '2024-09'
    GROUP BY cohort_month
    ORDER BY cohort_month
    """
    return pd.read_sql(query, conn)

# HEADER
st.markdown('<div class="main-header">📊 Subscription Health Dashboard</div>', unsafe_allow_html=True)
st.markdown('<div class="sub-header">Comprehensive Analysis of Customer Lifecycle & Business Metrics</div>', unsafe_allow_html=True)

# SIDEBAR
st.sidebar.title("🎛️ Dashboard Controls")
st.sidebar.markdown("---")

page = st.sidebar.radio(
    "Select View",
    ["📈 Overview", "📉 Churn Analysis", "🔄 Resurrection", "💰 Revenue", "💳 Payment Behavior", "📊 Cohort Retention"],
    index=0
)

st.sidebar.markdown("---")
st.sidebar.markdown("### About")
st.sidebar.info("""
**Data Period:** Jan 2023 - Jan 2025

**Metrics Tracked:**
- Churn Rate
- Resurrection Rate  
- Revenue per Customer
- Time to Payment
- Cohort Retention
""")

st.sidebar.markdown("---")
st.sidebar.markdown("### 📌 Key Findings")
st.sidebar.success("""
✅ **Strong resurrection**: 86.16%

⚠️ **Declining retention**: 2024 cohorts down 27.7pp

📈 **Payment improvement**: 87% faster
""")

# PAGE: OVERVIEW
if page == "📈 Overview":
    st.markdown('<div class="section-header">KPI Metrics</div>', unsafe_allow_html=True)
    
    df_metrics = load_key_metrics()
    
    col1, col2, col3, col4 = st.columns(4)
    
    with col1:
        st.metric(
            label="👥 Total Customers",
            value=f"{int(df_metrics['total_customers'].values[0]):,}",
            help="Total number of unique subscriptions"
        )
    
    with col2:
        churn_pct = df_metrics['churn_pct'].values[0]
        churned = int(df_metrics['churned_customers'].values[0])
        st.metric(
            label="📉 Churn Rate",
            value=f"{churn_pct}%",
            delta=f"-{churned:,} churned",
            delta_color="inverse",
            help="% of customers who cancelled"
        )
    
    with col3:
        res_pct = df_metrics['resurrection_pct'].values[0]
        resurrected = int(df_metrics['resurrected_customers'].values[0])
        st.metric(
            label="🔄 Resurrection Rate",
            value=f"{res_pct}%",
            delta=f"+{resurrected:,} returned",
            help="% of churned who resubscribed"
        )
    
    with col4:
        avg_rev = df_metrics['avg_revenue_per_customer'].values[0]
        st.metric(
            label="💰 Avg Revenue",
            value=f"${avg_rev:,.0f}",
            delta="Lifetime Value",
            delta_color="off",
            help="Avg revenue per customer"
        )
    
    st.markdown("---")
    col1, col2, col3, col4 = st.columns(4)
    
    with col1:
        active = int(df_metrics['active_customers'].values[0])
        active_pct = df_metrics['active_pct'].values[0]
        st.metric("✅ Active", f"{active:,}", f"{active_pct}% of total")
    
    with col2:
        st.metric("📊 Monthly Churn", "2.54%", "Stable")
    
    with col3:
        st.metric("⏱️ Avg Lifetime", "340 days", "~11 months")
    
    with col4:
        total_rev = df_metrics['total_revenue'].values[0]
        st.metric("💵 Total Revenue", f"${total_rev/1000000:.2f}M")
    
    st.markdown("---")
    st.markdown('<div class="section-header">Trend Analysis</div>', unsafe_allow_html=True)
    
    col1, col2 = st.columns(2)
    
    with col1:
        df_churn = load_churn_data()
        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=df_churn['month'], y=df_churn['churn_rate_pct'],
            mode='lines+markers', line=dict(color='#E74C3C', width=3),
            fill='tozeroy', fillcolor='rgba(231, 76, 60, 0.1)'
        ))
        fig.add_hline(y=df_churn['churn_rate_pct'].mean(), line_dash="dash",
                     annotation_text=f"Avg: {df_churn['churn_rate_pct'].mean():.2f}%")
        fig.update_layout(title="Monthly Churn Rate", height=400, plot_bgcolor='white')
        st.plotly_chart(fig, use_container_width=True)
    
    with col2:
        active = int(df_metrics['active_customers'].values[0])
        churned = int(df_metrics['churned_customers'].values[0])
        fig = go.Figure(data=[go.Pie(
            labels=['Active', 'Churned'], values=[active, churned],
            marker=dict(colors=['#2ECC71', '#E74C3C']), hole=.4
        )])
        fig.update_layout(title="Status Distribution", height=400)
        st.plotly_chart(fig, use_container_width=True)

elif page == "📉 Churn Analysis":
    st.markdown('<div class="section-header">Churn Analysis</div>', unsafe_allow_html=True)
    df_churn = load_churn_data()
    
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Avg Churn", f"{df_churn['churn_rate_pct'].mean():.2f}%")
    with col2:
        st.metric("Peak", df_churn.loc[df_churn['churn_rate_pct'].idxmax(), 'month'])
    with col3:
        st.metric("Lowest", df_churn.loc[df_churn['churn_rate_pct'].idxmin(), 'month'])
    
    st.markdown("---")
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    fig.add_trace(go.Bar(x=df_churn['month'], y=df_churn['churned_customers'], 
                         name='Volume', marker_color='#E74C3C'), secondary_y=False)
    fig.add_trace(go.Scatter(x=df_churn['month'], y=df_churn['churn_rate_pct'],
                            name='Rate %', line=dict(width=3)), secondary_y=True)
    fig.update_layout(title="Churn: Volume vs Rate", height=500)
    st.plotly_chart(fig, use_container_width=True)

# FOOTER
st.markdown("---")
st.markdown("""
<div style='text-align: center; color: #95A5A6; padding: 20px;'>
    <p><strong>Subscription Health Dashboard</strong></p>
    <p>Data: Jan 2023 - Jan 2025 | By Pedro Blanco</p>
</div>
""", unsafe_allow_html=True)
EOF
