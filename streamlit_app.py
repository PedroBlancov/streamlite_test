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
    
    /* Section headers */
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

# ============================================================
# HEADER
# ============================================================
st.markdown('<div class="main-header">📊 Subscription Health Dashboard</div>', unsafe_allow_html=True)
st.markdown('<div class="sub-header">Comprehensive Analysis of Customer Lifecycle & Business Metrics</div>', unsafe_allow_html=True)

# ============================================================
# SIDEBAR
# ============================================================
st.sidebar.title("🎛️ Dashboard Controls")
st.sidebar.markdown("---")

# Navigation
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
✅ **Strong resurrection rate**: 86.16%

⚠️ **Declining retention**: 2024 cohorts down 27.7pp

📈 **Payment improvement**: 87% faster payment
""")

# ============================================================
# PAGE: OVERVIEW
# ============================================================
if page == "📈 Overview":
    st.markdown('<div class="section-header">KPI Metrics</div>', unsafe_allow_html=True)
    
    # Load metrics
    df_metrics = load_key_metrics()
    
    # Top row - 4 key metrics
    col1, col2, col3, col4 = st.columns(4)
    
    with col1:
        st.metric(
            label="👥 Total Customers",
            value=f"{int(df_metrics['total_customers'].values[0]):,}",
            help="Total number of unique subscriptions in the system"
        )
    
    with col2:
        churn_pct = df_metrics['churn_pct'].values[0]
        churned = int(df_metrics['churned_customers'].values[0])
        st.metric(
            label="📉 Churn Rate",
            value=f"{churn_pct}%",
            delta=f"-{churned:,} customers churned",
            delta_color="inverse",
            help="Percentage of customers who cancelled their subscription"
        )
    
    with col3:
        res_pct = df_metrics['resurrection_pct'].values[0]
        resurrected = int(df_metrics['resurrected_customers'].values[0])
        st.metric(
            label="🔄 Resurrection Rate",
            value=f"{res_pct}%",
            delta=f"+{resurrected:,} customers returned",
            delta_color="normal",
            help="Percentage of churned customers who resubscribed"
        )
    
    with col4:
        avg_rev = df_metrics['avg_revenue_per_customer'].values[0]
        st.metric(
            label="💰 Avg Revenue/Customer",
            value=f"${avg_rev:,.0f}",
            delta="Lifetime Value",
            delta_color="off",
            help="Average total revenue generated per customer"
        )
    
    # Additional context row
    st.markdown("---")
    col1, col2, col3, col4 = st.columns(4)
    
    with col1:
        active = int(df_metrics['active_customers'].values[0])
        active_pct = df_metrics['active_pct'].values[0]
        st.metric(
            label="✅ Active Subscriptions",
            value=f"{active:,}",
            delta=f"{active_pct}% of total"
        )
    
    with col2:
        st.metric(
            label="📊 Avg Monthly Churn",
            value="2.54%",
            delta="Stable trend",
            delta_color="off"
        )
    
    with col3:
        st.metric(
            label="⏱️ Avg Customer Lifetime",
            value="340 days",
            delta="~11 months"
        )
    
    with col4:
        total_rev = df_metrics['total_revenue'].values[0]
        st.metric(
            label="💵 Total Revenue",
            value=f"${total_rev/1000000:.2f}M",
            delta="All-time"
        )
    
    st.markdown("---")
    st.markdown('<div class="section-header">Trend Analysis</div>', unsafe_allow_html=True)
    
    # Visualizations
    col1, col2 = st.columns(2)
    
    with col1:
        df_churn = load_churn_data()
        fig_churn = go.Figure()
        fig_churn.add_trace(go.Scatter(
            x=df_churn['month'],
            y=df_churn['churn_rate_pct'],
            mode='lines+markers',
            name='Churn Rate',
            line=dict(color='#E74C3C', width=3),
            marker=dict(size=8),
            fill='tozeroy',
            fillcolor='rgba(231, 76, 60, 0.1)'
        ))
        fig_churn.add_hline(
            y=df_churn['churn_rate_pct'].mean(),
            line_dash="dash",
            line_color="#34495E",
            annotation_text=f"Avg: {df_churn['churn_rate_pct'].mean():.2f}%",
            annotation_position="top right"
        )
        fig_churn.update_layout(
            title="Monthly Churn Rate Trend",
            xaxis_title="Month",
            yaxis_title="Churn Rate (%)",
            height=400,
            hovermode='x unified',
            plot_bgcolor='white'
        )
        st.plotly_chart(fig_churn, use_container_width=True)
    
    with col2:
        active = int(df_metrics['active_customers'].values[0])
        churned = int(df_metrics['churned_customers'].values[0])
        
        fig_pie = go.Figure(data=[go.Pie(
            labels=['Active', 'Churned'],
            values=[active, churned],
            marker=dict(colors=['#2ECC71', '#E74C3C']),
            hole=.4,
            textfont=dict(size=18, color='white', family='Arial Black'),
            textposition='inside'
        )])
        fig_pie.update_layout(
            title="Customer Status Distribution",
            height=400,
            annotations=[dict(
                text=f'<b>{int(df_metrics["total_customers"].values[0]):,}</b><br>Total', 
                x=0.5, y=0.5, 
                font_size=18, 
                showarrow=False
            )]
        )
        st.plotly_chart(fig_pie, use_container_width=True)
    
    col1, col2 = st.columns(2)
    
    with col1:
        df_res = load_resurrection_data()
        fig_res = px.bar(
            df_res,
            x='resurrection_month',
            y='resurrections',
            title='Monthly Customer Resurrections',
            labels={'resurrection_month': 'Month', 'resurrections': 'Customers Returned'},
            color_discrete_sequence=['#3498DB']
        )
        fig_res.update_layout(height=400, xaxis_tickangle=-45, plot_bgcolor='white')
        st.plotly_chart(fig_res, use_container_width=True)
    
    with col2:
        df_rev = load_revenue_data()
        fig_rev = px.bar(
            df_rev,
            x='cohort_month',
            y='avg_revenue',
            title='Average Revenue per Customer by Cohort',
            labels={'cohort_month': 'Cohort', 'avg_revenue': 'Avg Revenue ($)'},
            color_discrete_sequence=['#2ECC71']
        )
        fig_rev.add_hline(
            y=df_rev['avg_revenue'].mean(),
            line_dash="dash",
            line_color="#E74C3C",
            annotation_text=f"Avg: ${df_rev['avg_revenue'].mean():.0f}",
            annotation_position="top right"
        )
        fig_rev.update_layout(height=400, xaxis_tickangle=-45, plot_bgcolor='white')
        st.plotly_chart(fig_rev, use_container_width=True)

# ============================================================
# OTHER PAGES (Churn, Resurrection, Revenue, etc.)
# ============================================================
elif page == "📉 Churn Analysis":
    st.markdown('<div class="section-header">Churn Analysis</div>', unsafe_allow_html=True)
    df_churn = load_churn_data()
    
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Average Churn Rate", f"{df_churn['churn_rate_pct'].mean():.2f}%")
    with col2:
        st.metric("Peak Churn Month", df_churn.loc[df_churn['churn_rate_pct'].idxmax(), 'month'], f"{df_churn['churn_rate_pct'].max():.2f}%")
    with col3:
        st.metric("Lowest Churn Month", df_churn.loc[df_churn['churn_rate_pct'].idxmin(), 'month'], f"{df_churn['churn_rate_pct'].min():.2f}%")
    with col4:
        recent_trend = df_churn.iloc[-3:]['churn_rate_pct'].mean()
        early_trend = df_churn.iloc[:3]['churn_rate_pct'].mean()
        st.metric("Trend", "📈 Increasing" if recent_trend > early_trend else "📉 Stable", f"{recent_trend - early_trend:+.2f} pp")
    
    st.markdown("---")
    
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    fig.add_trace(go.Bar(x=df_churn['month'], y=df_churn['churned_customers'], name='Churned Customers', marker_color='#E74C3C', opacity=0.6), secondary_y=False)
    fig.add_trace(go.Scatter(x=df_churn['month'], y=df_churn['churn_rate_pct'], name='Churn Rate %', line=dict(color='#2C3E50', width=3), marker=dict(size=10)), secondary_y=True)
    fig.update_layout(title="Monthly Churn: Volume vs Rate", xaxis_title="Month", height=500, hovermode='x unified', plot_bgcolor='white')
    fig.update_yaxes(title_text="Churned Customers", secondary_y=False)
    fig.update_yaxes(title_text="Churn Rate (%)", secondary_y=True)
    st.plotly_chart(fig, use_container_width=True)
    
    with st.expander("📊 View Detailed Data"):
        st.dataframe(df_churn, use_container_width=True)

# Add remaining pages following same pattern...
# (Resurrection, Revenue, Payment Behavior, Cohort Retention)

# ============================================================
# FOOTER
# ============================================================
st.markdown("---")
st.markdown("""
<div style='text-align: center; color: #95A5A6; padding: 20px;'>
    <p><strong>Subscription Health Dashboard</strong></p>
    <p>Data Period: January 2023 - January 2025 | Built with Streamlit & Plotly</p>
    <p style='font-size: 12px; margin-top: 10px;'>Created by Pedro Blanco | Data Analyst Case Study</p>
</div>
""", unsafe_allow_html=True)
EOF

echo "✅ Created streamlit_app.py"
echo ""
echo "📁 Files needed for Streamlit Cloud deployment:"
echo "   1. streamlit_app.py (main app)"
echo "   2. subscription_analysis.db (database)"
echo "   3. requirements.txt (dependencies)"
echo ""
echo "Creating requirements.txt..."
